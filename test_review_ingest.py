import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import review_ingest
import sheets_writer
from test_sheets_writer import FakeWorksheet

NOW = "2026-09-29T13:00:00+09:00"


class CountingWorksheet(FakeWorksheet):
    def __init__(self, initial_values=None):
        super().__init__(initial_values)
        self.append_rows_calls = 0

    def append_rows(self, rows, value_input_option=None):
        self.append_rows_calls += 1
        super().append_rows(rows, value_input_option)


def test_ingest_dedups_classifies_and_appends_once():
    ws = CountingWorksheet(initial_values=[sheets_writer.HEADER])
    existing = [{"hospital_id": "h_a", "channel": "네이버", "content": "친절해요", "date": "9.16.수", "collected_at": NOW}]
    reviews = [
        {"channel": "네이버", "author": "", "rating": None, "date": "9.16.수", "content": "친절해요 ", "has_reply": None},
        {"channel": "네이버", "author": "", "rating": None, "date": "9.17.목", "content": "친절해요", "has_reply": None},
        {"channel": "네이버", "author": "", "rating": None, "date": "9.18.금", "content": "새 리뷰 좋아요", "has_reply": False},
    ]

    added = review_ingest.ingest(ws, existing, "h_a", reviews, NOW)

    assert added == 2, f"실제: {added}"
    assert ws.append_rows_calls == 1
    assert [row[sheets_writer.HEADER.index("date")] for row in ws.values[1:]] == ["9.17.목", "9.18.금"]
    assert all(row[sheets_writer.HEADER.index("method")] for row in ws.values[1:]), "분류 결과가 기록돼야 함"
    assert ws.values[1][sheets_writer.HEADER.index("collected_at")] == NOW
    print("PASS: test_ingest_dedups_classifies_and_appends_once")


def test_ingest_no_new_reviews_makes_no_api_call():
    ws = CountingWorksheet(initial_values=[sheets_writer.HEADER])
    existing = [{"hospital_id": "h_a", "channel": "카카오맵", "content": "좋아요", "collected_at": NOW}]
    added = review_ingest.ingest(ws, existing, "h_a", [{"channel": "카카오맵", "content": "좋아요"}], NOW)
    assert added == 0
    assert ws.append_rows_calls == 0
    print("PASS: test_ingest_no_new_reviews_makes_no_api_call")


def test_ingest_skips_excluded_reviews():
    # 2026-10-14: 이용자가 다른 업체 리뷰를 병원 페이지에 잘못 올린 건은 시트에서 지우고,
    # 다음 수집 때 "새 리뷰"로 다시 들어오지 않게 제외 목록으로 막는다(본문 공백 차이는 무시).
    ws = CountingWorksheet(initial_values=[sheets_writer.HEADER])
    excluded = [{"hospital_id": "h_a", "channel": "네이버", "content": "그랜져 앞유리 교체\n잘 됐어요"}]
    reviews = [
        {"channel": "네이버", "content": "그랜져 앞유리 교체 잘 됐어요", "date": "9.18.금"},
        {"channel": "네이버", "content": "치료 잘 받았어요", "date": "9.18.금"},
    ]
    added = review_ingest.ingest(ws, [], "h_a", reviews, NOW, excluded=excluded)
    assert added == 1, f"실제: {added}"
    assert ws.values[1][sheets_writer.HEADER.index("content")] == "치료 잘 받았어요"
    # 다른 병원의 같은 본문은 막지 않는다
    assert review_ingest.ingest(ws, [], "h_b", reviews[:1], NOW, excluded=excluded) == 1
    print("PASS: test_ingest_skips_excluded_reviews")


def test_load_excluded_reviews_file():
    entries = review_ingest.load_excluded()
    assert isinstance(entries, list)
    for e in entries:
        assert e["hospital_id"] and e["channel"] and e["content"], f"필수 필드 누락: {e}"
    print("PASS: test_load_excluded_reviews_file")


if __name__ == "__main__":
    test_ingest_dedups_classifies_and_appends_once()
    test_ingest_no_new_reviews_makes_no_api_call()
    test_ingest_skips_excluded_reviews()
    test_load_excluded_reviews_file()
