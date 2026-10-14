"""Claude Code 검수(재분류) 단계.

`main.py`(매일 자동 수집, GitHub Actions, 완전 무인)와 별도로 동작한다.
Claude Code 세션이 켜져 있을 때만 /loop 등으로 주기 실행되어, method=="lexicon"인
시트 행(규칙기반 감성분류)을 Claude가 다시 판단해 sentiment/confirmed/method만
갱신한다. 설계: docs/superpowers/specs/2026-09-21-claude-reclassify-review-design.md

긍정·부정 리뷰(별점 판정 포함)는 Claude가 주요 칭찬/불만 포인트를 key_points 컬럼에 적는다 —
대시보드 긍정·부정 키워드 TOP 5의 원천. 설계: docs/superpowers/specs/2026-09-29-negative-key-points-design.md
"""

import argparse
import datetime
import json
import os
import re
from collections import Counter
from pathlib import Path

import dedup
import sheets_writer

WORK_DIR = Path(__file__).resolve().parent / "reclassify_work"
COMPETITORS_CONFIG_PATH = Path(__file__).resolve().parent / "config" / "competitors_config.json"
COMPETITOR_RECENT_MONTHS = 3
_KST = datetime.timezone(datetime.timedelta(hours=9))
REQUEST_FILENAME = "[재분류요청].json"
RESULT_FILENAME = "[재분류결과].json"
LOG_FILENAME = "reclassify_log.jsonl"


_KEY_POINT_SENTIMENTS = ("부정", "긍정")


def _split_key_points(value):
    return [p.strip() for p in str(value or "").split(",") if p.strip()]


def _tasks_for(review):
    """이 행에 Claude가 할 일. 감성 재판정은 규칙기반(lexicon) 행만, 주요 포인트는
    본문이 있고 아직 포인트가 없는 긍정·부정 행(별점 판정 포함) — 감성 작업 행은 판단 결과가
    긍정·부정일 수 있어 포인트 작업도 같이 받는다. 중립은 포인트를 쓰지 않는다."""
    if review.get("method") == "lexicon":
        return ["sentiment", "key_points"]
    if (review.get("sentiment") in _KEY_POINT_SENTIMENTS and str(review.get("content") or "").strip()
            and not _split_key_points(review.get("key_points"))):
        return ["key_points"]
    return []


def review_date(review):
    """시트 date 컬럼 → datetime.date (index.html normalizeDate와 같은 규칙). 모르면 None."""
    raw = str(review.get("date") or "").strip()
    if not raw:
        return None
    m = re.match(r"^(\d{4})-(\d{2})(?:-(\d{2}))?", raw)
    if review.get("channel") == "구글" and m:
        return datetime.date(int(m[1]), int(m[2]), int(m[3] or 1))
    m = re.match(r"^(\d{4})\.(\d{2})\.(\d{2})\.?$", raw)
    if m:
        return datetime.date(int(m[1]), int(m[2]), int(m[3]))
    naver = dedup.parse_naver_date(raw, review.get("collected_at"))
    if naver:
        return datetime.date.fromisoformat(naver)
    m = re.match(r"^(\d+)\s*(분|시간|일)\s*전$", raw)
    if m:
        try:
            collected = datetime.datetime.fromisoformat(str(review.get("collected_at") or ""))
        except ValueError:
            return None
        unit = {"분": "minutes", "시간": "hours", "일": "days"}[m[2]]
        moment = collected - datetime.timedelta(**{unit: int(m[1])})
        if moment.tzinfo is not None:
            moment = moment.astimezone(_KST)
        return moment.date()
    return None


def _months_ago(today, months):
    """달력 기준 N개월 전 같은 날(말일 보정) — 대시보드 '최근 N개월'과 맞춘다."""
    year, month = divmod(today.year * 12 + today.month - 1 - months, 12)
    month += 1
    for day in (today.day, 30, 29, 28):
        try:
            return datetime.date(year, month, day)
        except ValueError:
            continue


def load_competitor_ids():
    if not COMPETITORS_CONFIG_PATH.exists():
        return set()
    data = json.loads(COMPETITORS_CONFIG_PATH.read_text(encoding="utf-8"))
    return {c["competitor_id"] for c in data.get("competitors", [])}


def find_candidates(existing_reviews, competitor_ids=(), today=None, limit=None):
    """Claude에게 보낼 후보 목록(작업 종류 tasks 포함).

    경쟁병원 행은 최근 COMPETITOR_RECENT_MONTHS개월(작성일 기준) 리뷰만 후보로 낸다 — 2026-10-14
    사용자 결정(초기수집 ~1.8만 건 중 대시보드 기본 조회 기간만 재분류). 작성일을 모르면 제외한다.
    limit이 있으면 자사 병원 후보를 먼저 채우고 남는 자리에 경쟁병원 후보를 넣는다."""
    today = today or datetime.datetime.now(_KST).date()
    since = _months_ago(today, COMPETITOR_RECENT_MONTHS)
    competitor_ids = set(competitor_ids)
    own, competitors = [], []
    for review in existing_reviews:
        tasks = _tasks_for(review)
        if not tasks:
            continue
        if review.get("hospital_id") in competitor_ids:
            written = review_date(review)
            if written is None or written < since:
                continue
            competitors.append((review, tasks))
        else:
            own.append((review, tasks))
    picked = own + competitors
    if limit is not None:
        picked = picked[:limit]

    candidates = []
    for review, tasks in picked:
        matched_words = review.get("matched_words") or ""
        candidates.append({
            "id": len(candidates),
            "hospital_id": review.get("hospital_id"),
            "channel": review.get("channel"),
            "content": review.get("content"),
            "current_sentiment": review.get("sentiment"),
            "current_score": review.get("score"),
            "current_matched_words": [w for w in str(matched_words).split(",") if w],
            "tasks": tasks,
        })
    return candidates


def count_key_points(existing_reviews):
    """지금까지 쓴 주요 포인트와 건수(감성별) — 같은 칭찬/불만을 같은 말로 쓰게 요청 파일에 넣는다.
    칭찬과 불만 표현이 한 목록에 섞이지 않게 {"부정": {...}, "긍정": {...}}로 나눈다."""
    result = {}
    for sentiment in _KEY_POINT_SENTIMENTS:
        counts = Counter(p for r in existing_reviews if r.get("sentiment") == sentiment
                         for p in _split_key_points(r.get("key_points")) if p != "-")
        result[sentiment] = dict(counts.most_common())
    return result


def export_for_claude(candidates, work_dir, existing_key_points=None):
    """[재분류요청].json을 work_dir에 쓰고 경로를 반환한다.
    후보가 없으면 파일을 만들지 않고 None을 반환한다."""
    if not candidates:
        return None
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    path = work_dir / REQUEST_FILENAME
    payload = {
        "generated_at": datetime.datetime.now().astimezone().isoformat(),
        "existing_key_points": existing_key_points or {},
        "candidates": candidates,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def apply_results(worksheet, existing_reviews, work_dir):
    """[재분류결과].json을 읽어 시트에 반영한다. 실제로 바뀐 행 수를 반환한다.

    (hospital_id, channel, content)가 같고 후보와 같은 작업 대상(_tasks_for)인 행을 시트 순서대로
    하나씩 배정해 existing_reviews에서 실제 시트 행을 찾는다. 그 사이
    시트가 바뀌어 못 찾으면 그 건은 건너뛰고 경고만 출력, 나머지는 계속 진행한다."""
    work_dir = Path(work_dir)
    request_path = work_dir / REQUEST_FILENAME
    result_path = work_dir / RESULT_FILENAME
    if not request_path.exists():
        raise FileNotFoundError(f"{REQUEST_FILENAME}가 없습니다. 먼저 --export를 실행하세요.")
    if not result_path.exists():
        raise FileNotFoundError(f"{RESULT_FILENAME}가 없습니다. Claude가 검수 결과를 작성했는지 확인하세요.")

    candidates_by_id = {c["id"]: c for c in json.loads(request_path.read_text(encoding="utf-8"))["candidates"]}
    results = json.loads(result_path.read_text(encoding="utf-8"))["results"]

    row_updates = []
    log_entries = []
    claimed_indexes = set()
    for result in sorted(results, key=lambda r: r["id"]):
        candidate = candidates_by_id.get(result["id"])
        if candidate is None:
            print(f"경고: 결과 id={result['id']}에 해당하는 요청 후보를 찾을 수 없어 건너뜁니다.")
            continue

        # 본문이 같은 짧은 리뷰("굿", "좋아요" 등)가 여러 행일 수 있어, 같은 작업 대상이고
        # 이번 실행에서 쓰지 않은 행만 매칭한다 (2026-09-28 실측: 전부 첫 행에 반영되던 버그).
        tasks = candidate.get("tasks", ["sentiment", "key_points"])  # tasks 없는 옛 요청 파일 = lexicon 후보
        row_number = None
        for index, review in enumerate(existing_reviews):
            if (index in claimed_indexes
                    or _tasks_for(review) != tasks
                    or review.get("hospital_id") != candidate["hospital_id"]
                    or review.get("channel") != candidate["channel"]
                    or review.get("content") != candidate["content"]):
                continue
            claimed_indexes.add(index)
            row_number = index + 2  # 헤더가 1행이므로 실제 데이터 행은 +2
            break
        if row_number is None:
            print(f"경고: channel={candidate['channel']!r} content={str(candidate['content'])[:20]!r}... 에 해당하는 "
                  f"시트 행을 찾을 수 없어 건너뜁니다 (그 사이 시트가 바뀌었을 수 있음).")
            continue

        fields = {}
        if "sentiment" in tasks and "sentiment" in result:
            fields.update(sentiment=result["sentiment"], confirmed=result["confirmed"], method="claude_review")
        points = result.get("key_points") or []
        if isinstance(points, str):  # 리스트 대신 문자열로 적어도 한 개의 포인트로 본다("주,차"로 쪼개지지 않게)
            points = [points]
        points = [str(p).strip() for p in points if str(p).strip()]
        if "key_points" in tasks and points:
            fields["key_points"] = ",".join(points)
        if not fields:
            continue
        row_updates.append((row_number, fields))
        log_entries.append({
            "reviewed_at": datetime.datetime.now().astimezone().isoformat(),
            "hospital_id": candidate["hospital_id"],
            "channel": candidate["channel"],
            "content": candidate["content"],
            "old_sentiment": candidate["current_sentiment"],
            "new_sentiment": result.get("sentiment", candidate["current_sentiment"]),
            "key_points": result.get("key_points", []),
            "note": result.get("note", ""),
        })

    # 행마다 개별 API 호출하면 Sheets 쓰기 quota(분당 요청 수)를 초과할 수 있어
    # (2026-09-22 실측: 85행 x 3컬럼=255회 호출로 429 에러 발생) 한 번에 묶어 반영한다.
    sheets_writer.batch_update_fields(worksheet, row_updates)
    updated = len(row_updates)

    if log_entries:
        with open(work_dir / LOG_FILENAME, "a", encoding="utf-8") as f:
            for entry in log_entries:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    return updated


def run_export(limit=None):
    sheet_id = os.environ["GOOGLE_SHEET_ID"]
    credentials_json = os.environ["GCP_SA_KEY"]
    worksheet = sheets_writer.connect(sheet_id, credentials_json)
    sheets_writer.ensure_header(worksheet)  # key_points 컬럼이 없는 구버전 시트면 여기서 붙인다
    existing = sheets_writer.read_existing_reviews(worksheet)
    competitor_ids = load_competitor_ids()
    total = find_candidates(existing, competitor_ids)
    candidates = total if limit is None else find_candidates(existing, competitor_ids, limit=limit)
    path = export_for_claude(candidates, WORK_DIR, count_key_points(existing))
    if path:
        own = sum(1 for c in total if c["hospital_id"] not in competitor_ids)
        print(f"재분류 후보 {len(candidates)}건 -> {path} "
              f"(전체 대기 {len(total)}건: 자사 {own} / 경쟁병원 최근 {COMPETITOR_RECENT_MONTHS}개월 {len(total) - own})")
    else:
        print("재분류·주요 포인트 대상이 없습니다.")


def run_apply():
    sheet_id = os.environ["GOOGLE_SHEET_ID"]
    credentials_json = os.environ["GCP_SA_KEY"]
    worksheet = sheets_writer.connect(sheet_id, credentials_json)
    existing = sheets_writer.read_existing_reviews(worksheet)
    updated = apply_results(worksheet, existing, WORK_DIR)
    print(f"{updated}건 반영 완료")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="리뷰 감성 재분류(Claude Code 검수)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--export", action="store_true", help="재분류 후보를 JSON으로 내보낸다")
    group.add_argument("--apply", action="store_true", help="Claude가 작성한 결과를 시트에 반영한다")
    parser.add_argument("--limit", type=int, help="--export 후보 최대 건수(자사 병원 먼저). 대량일 때 회차를 나눈다")
    args = parser.parse_args()

    if args.export:
        run_export(args.limit)
    else:
        run_apply()
