"""매일 자동 수집 파이프라인 진입점 (GitHub Actions에서 실행).

collectors(네이버/카카오/구글) -> dedup -> classifier(규칙기반) -> sheets_writer
순서로 병원별 신규 리뷰를 Google Sheets에 append한다(dedup -> 분류 -> 적재는
review_ingest.ingest 공통 함수 — 크롬 확장 브리지와 같은 경로). 사람/Claude 세션 개입이
필요 없는 완전 무인 실행을 전제로 한다(설계문서 2절 확정사항).
"""

import argparse
import datetime
import json
import math
import os
import sys
import time
from pathlib import Path

import review_ingest
import sheets_writer
from collectors import google, kakao, naver
from collectors.base import safe_run

CONFIG_PATH = Path(__file__).resolve().parent / "config" / "hospitals_config.json"
COMPETITORS_CONFIG_PATH = Path(__file__).resolve().parent / "config" / "competitors_config.json"

# 경쟁병원 순번 수집(2026-10-13 사용자 확정: 경쟁병원 몫 하루 90분). 하루 정원 = 90분 / 실측 평균 66초.
COMPETITOR_DAILY_CAPACITY = 80
COMPETITOR_TIME_BUDGET_SECONDS = 90 * 60
ROTATION_EPOCH = datetime.date(2026, 10, 14)
KST = datetime.timezone(datetime.timedelta(hours=9))

_CHANNEL_COLLECTORS = (
    ("네이버", naver, "naver_place_url"),
    ("카카오맵", kakao, "kakao_place_url"),
    ("구글", google, "google_place_id"),
)


def load_hospitals():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)["hospitals"]


def load_competitors():
    """경쟁 병원도 동일한 hospital 형태({hospital_id, hospital_name, channels})로
    반환해, collect_hospital 이하 파이프라인을 그대로 재사용할 수 있게 한다.
    naver_place_url이 빈 문자열인 곳은 그 채널을 못 찾은 것 — 채널만 건너뛴다."""
    if not COMPETITORS_CONFIG_PATH.exists():
        return []
    with open(COMPETITORS_CONFIG_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return [
        {
            "hospital_id": c["competitor_id"],
            "hospital_name": c["name"],
            "channels": c["channels"],
            "for_hospitals": c.get("for_hospitals", []),
        }
        for c in data.get("competitors", [])
    ]


def competitor_group_count(n_competitors, capacity=COMPETITOR_DAILY_CAPACITY):
    return max(1, math.ceil(n_competitors / capacity))


def todays_competitors(competitors, today, capacity=COMPETITOR_DAILY_CAPACITY):
    """날짜만으로 오늘 수집할 그룹을 정한다(상태 저장 없음, 같은 날 재실행해도 같은 그룹).
    competitor_id 순으로 정렬해 k번째를 k % G 그룹에 교차 배정 → 모든 경쟁병원이 G일에 한 번씩 수집된다."""
    ordered = sorted(competitors, key=lambda c: c["hospital_id"])
    n_groups = competitor_group_count(len(ordered), capacity)
    group = (today - ROTATION_EPOCH).days % n_groups
    return group, [c for k, c in enumerate(ordered) if k % n_groups == group]


def select_competitors(competitors, today, override, capacity=COMPETITOR_DAILY_CAPACITY):
    """override: ""=오늘 그룹, "all"=전체, 숫자=그 그룹(수동 실행용 COMPETITOR_GROUP)."""
    override = (override or "").strip()
    if override == "all":
        return sorted(competitors, key=lambda c: c["hospital_id"])
    if override.isdigit():
        ordered = sorted(competitors, key=lambda c: c["hospital_id"])
        n_groups = competitor_group_count(len(ordered), capacity)
        g = int(override) % n_groups
        return [c for k, c in enumerate(ordered) if k % n_groups == g]
    return todays_competitors(competitors, today, capacity)[1]


def collect_hospital(hospital):
    """모든 채널을 수집해 (전체 리뷰 목록, 실패 메시지 목록)을 반환한다.
    채널 URL이 설정 안 된 경우(경쟁병원 일부처럼)는 호출 자체를 건너뛰고
    에러로 취급하지 않는다. 설정된 채널이 실패하면 나머지는 계속 진행한다
    (설계문서 7절)."""
    raw_reviews = []
    errors = []
    channels = hospital.get("channels", {})
    for channel_name, collector_module, config_key in _CHANNEL_COLLECTORS:
        if not channels.get(config_key):
            continue
        result, err = safe_run(hospital["hospital_id"], channel_name, collector_module.collect_full, hospital)
        if err:
            errors.append(err)
        else:
            raw_reviews.extend(result)
    return raw_reviews, errors


def run(hospital_id_filter=None):
    own = load_hospitals()
    competitors = load_competitors()
    if hospital_id_filter:
        own = [h for h in own if h["hospital_id"] == hospital_id_filter]
        competitors = [h for h in competitors if h["hospital_id"] == hospital_id_filter]
        if not own and not competitors:
            print(f"등록되지 않은 hospital_id입니다: {hospital_id_filter!r}")
            return
    else:
        today = datetime.datetime.now(KST).date()
        override = os.environ.get("COMPETITOR_GROUP", "")
        total = len(competitors)
        group_label = override or todays_competitors(competitors, today)[0]
        competitors = select_competitors(competitors, today, override)
        print(f"경쟁병원 {total}곳 중 오늘 {len(competitors)}곳 수집 "
              f"(그룹 {group_label} / 전체 {competitor_group_count(total)}그룹)")

    sheet_id = os.environ["GOOGLE_SHEET_ID"]
    credentials_json = os.environ["GCP_SA_KEY"]
    worksheet = sheets_writer.connect(sheet_id, credentials_json)
    sheets_writer.ensure_header(worksheet)

    all_existing = sheets_writer.read_existing_reviews(worksheet)
    collected_at = datetime.datetime.now().astimezone().isoformat()
    failed = []

    def collect_and_ingest(hospital):
        hospital_id = hospital["hospital_id"]
        raw_reviews, errors = collect_hospital(hospital)
        for err in errors:
            print(err)

        existing_for_hospital = [r for r in all_existing if r.get("hospital_id") == hospital_id]
        try:
            added = review_ingest.ingest(worksheet, existing_for_hospital, hospital_id, raw_reviews, collected_at)
        except Exception as e:
            # 2026-10-22: 한 병원의 시트 쓰기 오류로 수집 전체가 멈췄다 → 그 병원만 실패로 남기고 계속한다.
            # 실행은 마지막에 실패로 끝내, 그날 PC 보완 수집(scheduled/daily_job.py)이 다시 수집하게 한다.
            failed.append(hospital_id)
            print(f"::warning title=시트 반영 실패::{hospital_id}: {e}")
            return
        print(f"{hospital_id}: 수집 {len(raw_reviews)}건 중 신규 {added}건 반영 (실패 채널 {len(errors)}건)")

    # 자사 병원은 매일 전부 수집한다
    for hospital in own:
        collect_and_ingest(hospital)

    # 경쟁병원은 시간 예산 안에서만 — 넘기면 남은 곳은 다음 주기로 미룬다
    started = time.monotonic()
    for i, hospital in enumerate(competitors):
        if time.monotonic() - started >= COMPETITOR_TIME_BUDGET_SECONDS:
            print(f"::warning title=경쟁병원 수집 시간 예산 초과::{COMPETITOR_TIME_BUDGET_SECONDS // 60}분을 넘겨 "
                  f"{len(competitors) - i}곳을 건너뛰었습니다. 반복되면 COMPETITOR_DAILY_CAPACITY를 낮추세요.")
            break
        collect_and_ingest(hospital)

    if failed:
        print(f"::error title=시트 반영 실패 {len(failed)}곳::{', '.join(failed)}")
    return failed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="병원 리뷰 매일 자동 수집")
    parser.add_argument("--hospital", help="특정 hospital_id만 실행 (생략 시 전체 병원)")
    args = parser.parse_args()
    sys.exit(1 if run(args.hospital) else 0)
