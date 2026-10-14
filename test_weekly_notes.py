import datetime
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))

import weekly_notes

TODAY = datetime.date(2026, 10, 19)  # 월요일
META = {"h_a": {"name": "가병원"}, "h_b": {"name": "나병원"}}


def _row(hospital_id, date, content="친절해요", sentiment="긍정", key_points="직원 친절", channel="카카오맵"):
    return {"hospital_id": hospital_id, "channel": channel, "date": date, "content": content,
            "sentiment": sentiment, "key_points": key_points, "collected_at": "2026-10-19T13:30:00+09:00"}


def test_build_request_groups_last_7_days_including_today():
    rows = [
        _row("h_a", "2026.10.13."),            # 7일 전 시작일(10-13) 포함
        _row("h_a", "2026.10.19."),            # 오늘 포함
        _row("h_a", "2026.10.12."),            # 기간 밖
        _row("h_b", "2026.10.01."),            # 기간 밖 → 7일 리뷰 0건, 마지막 리뷰일만
        _row("comp_k1", "2026.10.18."),        # 경쟁병원은 대상 아님
    ]
    req = weekly_notes.build_request(rows, META, TODAY)
    assert (req["period_start"], req["period_end"]) == ("2026-10-13", "2026-10-19")
    assert set(req["hospitals"]) == {"h_a", "h_b"}
    assert req["hospitals"]["h_a"]["count"] == 2
    assert [r["date"] for r in req["hospitals"]["h_a"]["reviews"]] == ["2026-10-13", "2026-10-19"]
    assert req["hospitals"]["h_b"]["count"] == 0
    assert req["hospitals"]["h_b"]["last_review_date"] == "2026-10-01"
    print("PASS: test_build_request_groups_last_7_days_including_today")


def test_build_notes_validates_and_takes_count_from_request():
    req = weekly_notes.build_request([_row("h_a", "2026.10.19.")], META, TODAY)
    result = {"notes": {
        "h_a": {"flag": "none", "count": 99, "summary": "1건 긍정으로 직원 친절이 언급됐습니다."},
        "h_b": {"flag": "none", "summary": "최근 7일 새 리뷰가 없습니다."},
    }}
    notes = weekly_notes.build_notes(req, result, "2026-10-19T14:00:00+09:00")
    assert notes["period_start"] == "2026-10-13" and notes["period_end"] == "2026-10-19"
    assert notes["notes"]["h_a"]["count"] == 1, "건수는 Claude가 쓴 값이 아니라 요청 파일 값"
    assert notes["notes"]["h_b"]["count"] == 0
    print("PASS: test_build_notes_validates_and_takes_count_from_request")


def test_build_notes_rejects_missing_hospital_or_bad_flag():
    req = weekly_notes.build_request([], META, TODAY)
    for bad in (
        {"notes": {"h_a": {"flag": "none", "summary": "요약"}}},                                   # h_b 누락
        {"notes": {"h_a": {"flag": "nope", "summary": "요약"}, "h_b": {"flag": "none", "summary": "요약"}}},
        {"notes": {"h_a": {"flag": "none", "summary": " "}, "h_b": {"flag": "none", "summary": "요약"}}},
    ):
        try:
            weekly_notes.build_notes(req, bad, "2026-10-19T14:00:00+09:00")
        except ValueError:
            continue
        raise AssertionError(f"검증 실패를 못 잡음: {bad}")
    print("PASS: test_build_notes_rejects_missing_hospital_or_bad_flag")


def test_is_due_monday_or_stale():
    assert weekly_notes.is_due(TODAY, {"period_end": "2026-10-18"})                        # 월요일
    assert not weekly_notes.is_due(datetime.date(2026, 10, 20), {"period_end": "2026-10-19"})
    assert weekly_notes.is_due(datetime.date(2026, 10, 21), {"period_end": "2026-10-12"})  # 월요일 놓침 → 이월
    assert weekly_notes.is_due(datetime.date(2026, 10, 21), None)
    # 같은 월요일에 두 번 돌지 않는다
    assert not weekly_notes.is_due(TODAY, {"period_end": "2026-10-19"})
    print("PASS: test_is_due_monday_or_stale")


def test_export_and_apply_files_roundtrip():
    work = tempfile.mkdtemp()
    try:
        out = os.path.join(work, "weekly_notes.json")
        path = weekly_notes.export_request([_row("h_a", "2026.10.19.")], META, TODAY, work)
        assert os.path.exists(path)
        with open(os.path.join(work, weekly_notes.RESULT_FILENAME), "w", encoding="utf-8") as f:
            json.dump({"notes": {"h_a": {"flag": "watch", "summary": "요약"}, "h_b": {"flag": "none", "summary": "없음"}}}, f, ensure_ascii=False)
        weekly_notes.apply_result(work, out)
        saved = json.load(open(out, encoding="utf-8"))
        assert saved["notes"]["h_a"] == {"flag": "watch", "count": 1, "summary": "요약"}
        print("PASS: test_export_and_apply_files_roundtrip")
    finally:
        shutil.rmtree(work)


if __name__ == "__main__":
    test_build_request_groups_last_7_days_including_today()
    test_build_notes_validates_and_takes_count_from_request()
    test_build_notes_rejects_missing_hospital_or_bad_flag()
    test_is_due_monday_or_stale()
    test_export_and_apply_files_roundtrip()
