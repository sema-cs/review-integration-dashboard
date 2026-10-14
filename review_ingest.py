"""신규 리뷰 적재 공통 경로: 제외 목록 → 중복 제거 → 규칙기반 분류 → 시트 append(1회).

main.py(GitHub Actions 자동 수집)와 naver_extension_bridge.py(크롬 확장 엑셀 보완)가
둘 다 이 함수만 호출해, 두 경로의 중복 판정·분류 규칙이 어긋나지 않게 한다.
"""

import json
from pathlib import Path

import dedup
import sheets_writer
from classifier.rule_based import classify

# 이용자가 다른 업체·다른 병원 리뷰를 잘못 올린 건(2026-10-14~). 시트에서 지운 뒤 다시 수집되지 않게 막는다.
EXCLUDED_PATH = Path(__file__).resolve().parent / "config" / "excluded_reviews.json"


def load_excluded():
    if not EXCLUDED_PATH.exists():
        return []
    with open(EXCLUDED_PATH, encoding="utf-8") as f:
        return json.load(f).get("reviews", [])


def _excluded_keys(entries, hospital_id):
    return {(e["channel"], dedup.normalize_content(e["content"])) for e in entries if e["hospital_id"] == hospital_id}


def ingest(worksheet, existing_for_hospital, hospital_id, reviews, collected_at, excluded=None):
    """existing_for_hospital: 그 병원의 기존 시트 행(dict). 추가된 행 수를 반환한다.
    excluded: 제외 목록(None이면 config/excluded_reviews.json)."""
    keys = _excluded_keys(load_excluded() if excluded is None else excluded, hospital_id)
    if keys:
        reviews = [r for r in reviews if (r.get("channel"), dedup.normalize_content(r.get("content"))) not in keys]
    new_reviews = dedup.find_new_reviews(existing_for_hospital, reviews, collected_at)
    classified = [(review, classify(review)) for review in new_reviews]
    return sheets_writer.append_reviews(worksheet, hospital_id, classified, collected_at)
