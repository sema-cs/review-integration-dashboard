import datetime
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))

import reclassify
from test_sheets_writer import FakeWorksheet
import sheets_writer


def _row(hospital_id="gangnam_jstar", channel="네이버", content="좋아요", sentiment="부정", confirmed=True, score=-4, matched_words="염증,스트레스", method="lexicon", key_points="", severity=""):
    return {
        "hospital_id": hospital_id, "channel": channel, "author": "홍길동", "rating": "",
        "date": "2026-08-01", "content": content, "has_reply": False,
        "sentiment": sentiment, "confirmed": confirmed, "score": score,
        "matched_words": matched_words, "method": method, "collected_at": "2026-09-16T00:00:00",
        "key_points": key_points, "severity": severity,
    }


def test_find_candidates_filters_lexicon_only():
    existing = [
        _row(method="lexicon"),
        _row(channel="카카오맵", content="별로예요", method="rating", matched_words=""),
    ]
    candidates = reclassify.find_candidates(existing)
    assert len(candidates) == 2, f"실제: {candidates}"
    assert candidates[0]["channel"] == "네이버" and candidates[0]["tasks"] == ["sentiment", "key_points", "severity"]
    assert candidates[0]["id"] == 0
    # 별점 판정 행은 감성은 건드리지 않고 주요 포인트만 (spec 3.2)
    assert candidates[1]["channel"] == "카카오맵" and candidates[1]["tasks"] == ["key_points", "severity"]
    print("PASS: test_find_candidates_filters_lexicon_only")


def test_find_candidates_splits_matched_words_into_list():
    existing = [_row(matched_words="염증,스트레스,친절")]
    candidates = reclassify.find_candidates(existing)
    assert candidates[0]["current_matched_words"] == ["염증", "스트레스", "친절"], f"실제: {candidates[0]}"
    print("PASS: test_find_candidates_splits_matched_words_into_list")


def test_find_candidates_empty_matched_words_becomes_empty_list():
    existing = [_row(matched_words="")]
    candidates = reclassify.find_candidates(existing)
    assert candidates[0]["current_matched_words"] == [], f"실제: {candidates[0]}"
    print("PASS: test_find_candidates_empty_matched_words_becomes_empty_list")


def test_export_for_claude_writes_json_and_returns_path():
    work_dir = tempfile.mkdtemp()
    try:
        candidates = reclassify.find_candidates([_row()])
        path = reclassify.export_for_claude(candidates, work_dir)
        assert path is not None
        assert os.path.exists(path)
        payload = json.loads(open(path, encoding="utf-8").read())
        assert "generated_at" in payload
        assert payload["candidates"] == candidates
        print("PASS: test_export_for_claude_writes_json_and_returns_path")
    finally:
        shutil.rmtree(work_dir)


def test_export_for_claude_returns_none_and_writes_no_file_when_no_candidates():
    work_dir = tempfile.mkdtemp()
    try:
        path = reclassify.export_for_claude([], work_dir)
        assert path is None
        assert not os.path.exists(os.path.join(work_dir, reclassify.REQUEST_FILENAME))
        print("PASS: test_export_for_claude_returns_none_and_writes_no_file_when_no_candidates")
    finally:
        shutil.rmtree(work_dir)


def test_apply_results_updates_matching_row_and_logs():
    work_dir = tempfile.mkdtemp()
    try:
        existing = [_row(content="염증 때문에 병원 갔는데 친절해서 좋았어요")]
        candidates = reclassify.find_candidates(existing)
        reclassify.export_for_claude(candidates, work_dir)

        result_payload = {
            "reviewed_at": "2026-09-22T10:00:00+09:00",
            "results": [{"id": 0, "sentiment": "긍정", "confirmed": True, "note": "내원 사유 설명일 뿐 불만 아님"}],
        }
        with open(os.path.join(work_dir, reclassify.RESULT_FILENAME), "w", encoding="utf-8") as f:
            json.dump(result_payload, f, ensure_ascii=False)

        ws = FakeWorksheet(initial_values=[sheets_writer.HEADER, sheets_writer.review_to_row(
            "gangnam_jstar",
            {"channel": "네이버", "author": "홍길동", "rating": None, "date": "2026-08-01",
             "content": "염증 때문에 병원 갔는데 친절해서 좋았어요", "has_reply": False},
            {"sentiment": "부정", "confirmed": True, "score": -4, "matched_words": ["염증", "스트레스"], "method": "lexicon"},
            "2026-09-16T00:00:00",
        )])

        updated = reclassify.apply_results(ws, existing, work_dir)

        assert updated == 1, f"실제: {updated}"
        assert ws.values[1][sheets_writer.HEADER.index("sentiment")] == "긍정"
        assert ws.values[1][sheets_writer.HEADER.index("confirmed")] is True
        assert ws.values[1][sheets_writer.HEADER.index("method")] == "claude_review"
        # score/matched_words는 그대로 남아야 한다
        assert ws.values[1][sheets_writer.HEADER.index("score")] == -4
        assert ws.values[1][sheets_writer.HEADER.index("matched_words")] == "염증,스트레스"

        log_path = os.path.join(work_dir, reclassify.LOG_FILENAME)
        assert os.path.exists(log_path)
        log_lines = open(log_path, encoding="utf-8").read().strip().splitlines()
        assert len(log_lines) == 1
        log_entry = json.loads(log_lines[0])
        assert log_entry["new_sentiment"] == "긍정"
        print("PASS: test_apply_results_updates_matching_row_and_logs")
    finally:
        shutil.rmtree(work_dir)


def test_apply_results_skips_when_row_not_found_in_existing_reviews():
    work_dir = tempfile.mkdtemp()
    try:
        existing = [_row(content="찾을 수 있는 리뷰")]
        candidates = reclassify.find_candidates(existing)
        reclassify.export_for_claude(candidates, work_dir)

        # existing_reviews를 apply 시점에 다른 내용으로 바꿔, 시트에서 매칭 실패를 재현
        existing_at_apply_time = [_row(content="그 사이 내용이 바뀐 리뷰")]

        result_payload = {"reviewed_at": "2026-09-22T10:00:00+09:00",
                           "results": [{"id": 0, "sentiment": "긍정", "confirmed": True, "note": ""}]}
        with open(os.path.join(work_dir, reclassify.RESULT_FILENAME), "w", encoding="utf-8") as f:
            json.dump(result_payload, f, ensure_ascii=False)

        ws = FakeWorksheet(initial_values=[sheets_writer.HEADER])
        updated = reclassify.apply_results(ws, existing_at_apply_time, work_dir)

        assert updated == 0, f"실제: {updated}"
        assert not os.path.exists(os.path.join(work_dir, reclassify.LOG_FILENAME))
        print("PASS: test_apply_results_skips_when_row_not_found_in_existing_reviews")
    finally:
        shutil.rmtree(work_dir)


def test_apply_results_maps_duplicate_contents_to_distinct_lexicon_rows():
    # 2026-09-28 실측 버그: "굿"처럼 본문이 같은 짧은 리뷰가 여러 행이면 전부 첫 행(이미
    # claude_review인 행)에 반영되고 나머지 lexicon 행은 그대로 남았다.
    work_dir = tempfile.mkdtemp()
    try:
        review = {"channel": "네이버", "author": "홍길동", "rating": None, "date": "2026-08-01",
                  "content": "굿", "has_reply": False}
        reviewed = {"sentiment": "긍정", "confirmed": True, "score": 0, "matched_words": [], "method": "claude_review"}
        lexicon = {"sentiment": "중립", "confirmed": False, "score": 0, "matched_words": [], "method": "lexicon"}
        rows = [
            sheets_writer.review_to_row("gangnam_jstar", review, reviewed, "2026-09-16T00:00:00"),
            sheets_writer.review_to_row("gangnam_jstar", review, lexicon, "2026-09-16T00:00:00"),
            sheets_writer.review_to_row("gangnam_jstar", review, lexicon, "2026-09-16T00:00:00"),
        ]
        ws = FakeWorksheet(initial_values=[sheets_writer.HEADER] + rows)
        existing = [
            _row(content="굿", sentiment="긍정", method="claude_review", matched_words=""),
            _row(content="굿", sentiment="중립", method="lexicon", matched_words=""),
            _row(content="굿", sentiment="중립", method="lexicon", matched_words=""),
        ]
        candidates = reclassify.find_candidates(existing)
        reclassify.export_for_claude(candidates, work_dir)
        # 이미 claude_review인 긍정 행도 주요 포인트 후보가 되므로, 감성 작업(lexicon) 후보의 id만 쓴다
        lexicon_ids = [c["id"] for c in candidates if "sentiment" in c["tasks"]]
        assert len(lexicon_ids) == 2, f"실제: {candidates}"
        result_payload = {"reviewed_at": "2026-09-29T10:00:00+09:00", "results": [
            {"id": i, "sentiment": "긍정", "confirmed": True, "note": "짧은 칭찬"} for i in lexicon_ids
        ]}
        with open(os.path.join(work_dir, reclassify.RESULT_FILENAME), "w", encoding="utf-8") as f:
            json.dump(result_payload, f, ensure_ascii=False)

        updated = reclassify.apply_results(ws, existing, work_dir)

        method_col = sheets_writer.HEADER.index("method")
        sentiment_col = sheets_writer.HEADER.index("sentiment")
        assert updated == 2, f"실제: {updated}"
        assert [ws.values[i][method_col] for i in (1, 2, 3)] == ["claude_review"] * 3, f"실제: {ws.values}"
        assert [ws.values[i][sentiment_col] for i in (2, 3)] == ["긍정", "긍정"]
        print("PASS: test_apply_results_maps_duplicate_contents_to_distinct_lexicon_rows")
    finally:
        shutil.rmtree(work_dir)


def test_apply_results_does_not_match_other_hospital_with_same_content():
    work_dir = tempfile.mkdtemp()
    try:
        existing = [
            _row(hospital_id="comp_other", content="좋아요", sentiment="중립", method="lexicon", matched_words=""),
            _row(hospital_id="gangnam_jstar", content="좋아요", sentiment="중립", method="lexicon", matched_words=""),
        ]
        candidates = reclassify.find_candidates(existing)
        # 요청에는 gangnam_jstar 후보만 남긴다
        reclassify.export_for_claude([candidates[1]], work_dir)
        result_payload = {"reviewed_at": "2026-09-29T10:00:00+09:00",
                          "results": [{"id": 1, "sentiment": "긍정", "confirmed": True, "note": ""}]}
        with open(os.path.join(work_dir, reclassify.RESULT_FILENAME), "w", encoding="utf-8") as f:
            json.dump(result_payload, f, ensure_ascii=False)

        review = {"channel": "네이버", "author": "홍길동", "rating": None, "date": "2026-08-01",
                  "content": "좋아요", "has_reply": False}
        lexicon = {"sentiment": "중립", "confirmed": False, "score": 0, "matched_words": [], "method": "lexicon"}
        ws = FakeWorksheet(initial_values=[sheets_writer.HEADER,
                                           sheets_writer.review_to_row("comp_other", review, lexicon, "t"),
                                           sheets_writer.review_to_row("gangnam_jstar", review, lexicon, "t")])

        reclassify.apply_results(ws, existing, work_dir)

        method_col = sheets_writer.HEADER.index("method")
        assert ws.values[1][method_col] == "lexicon", f"다른 병원 행이 바뀌면 안 됨: {ws.values[1]}"
        assert ws.values[2][method_col] == "claude_review"
        print("PASS: test_apply_results_does_not_match_other_hospital_with_same_content")
    finally:
        shutil.rmtree(work_dir)


def test_apply_results_raises_when_request_file_missing():
    work_dir = tempfile.mkdtemp()
    try:
        raised = False
        try:
            reclassify.apply_results(FakeWorksheet(), [], work_dir)
        except FileNotFoundError:
            raised = True
        assert raised, "요청 파일이 없으면 명확한 예외로 중단해야 함"
        print("PASS: test_apply_results_raises_when_request_file_missing")
    finally:
        shutil.rmtree(work_dir)



def _sheet_row(content, sentiment, method, key_points="", hospital_id="h_a", channel="구글", severity=""):
    review = {"channel": channel, "author": "", "rating": 1 if method == "rating" else None, "date": "",
              "content": content, "has_reply": False}
    row = sheets_writer.review_to_row(hospital_id, review, {"sentiment": sentiment, "confirmed": True, "score": None,
                                                            "matched_words": [], "method": method}, "2026-09-16T00:00:00")
    row[sheets_writer.HEADER.index("key_points")] = key_points
    row[sheets_writer.HEADER.index("severity")] = severity
    return row


def _run_apply(sheet_rows, existing, results):
    work_dir = tempfile.mkdtemp()
    try:
        reclassify.export_for_claude(reclassify.find_candidates(existing), work_dir)
        with open(os.path.join(work_dir, reclassify.RESULT_FILENAME), "w", encoding="utf-8") as f:
            json.dump({"reviewed_at": "2026-09-29T10:00:00+09:00", "results": results}, f, ensure_ascii=False)
        ws = FakeWorksheet(initial_values=[sheets_writer.HEADER] + sheet_rows)
        updated = reclassify.apply_results(ws, existing, work_dir)
        return ws, updated
    finally:
        shutil.rmtree(work_dir)


def test_find_candidates_adds_key_point_task_for_negative_rows():
    existing = [
        _row(channel="구글", content="주차장 입구를 막아요", sentiment="부정", method="rating", matched_words=""),
        _row(content="상담이 성의 없어요", sentiment="부정", method="claude_review"),
        _row(content="좋아요", sentiment="긍정", method="lexicon"),
    ]
    candidates = reclassify.find_candidates(existing)
    assert [c["tasks"] for c in candidates] == [["key_points", "severity"], ["key_points", "severity"], ["sentiment", "key_points", "severity"]], f"실제: {candidates}"
    print("PASS: test_find_candidates_adds_key_point_task_for_negative_rows")


def test_find_candidates_skips_rows_with_key_points():
    existing = [
        _row(content="주차가 불편", sentiment="부정", method="rating", key_points="주차", severity="일반"),
        _row(content="별로", sentiment="부정", method="claude_review", key_points="-", severity="일반"),
        _row(content="", sentiment="부정", method="rating"),
        _row(content="그냥 그래요", sentiment="중립", method="claude_review"),
    ]
    assert reclassify.find_candidates(existing) == []
    print("PASS: test_find_candidates_skips_rows_with_key_points")


def test_export_includes_existing_key_points():
    work_dir = tempfile.mkdtemp()
    try:
        existing = [_row(key_points="주차,가격", method="rating"), _row(key_points="주차", method="rating"),
                    _row(key_points="-", method="rating"), _row(content="새 불만", method="rating"),
                    _row(key_points="직원 친절", sentiment="긍정", method="claude_review")]
        path = reclassify.export_for_claude(reclassify.find_candidates(existing), work_dir,
                                            reclassify.count_key_points(existing))
        payload = json.loads(open(path, encoding="utf-8").read())
        # 칭찬·불만 표현이 섞이지 않게 감성별로 나눈다
        assert payload["existing_key_points"] == {"부정": {"주차": 2, "가격": 1}, "긍정": {"직원 친절": 1}},             f"실제: {payload['existing_key_points']}"
        print("PASS: test_export_includes_existing_key_points")
    finally:
        shutil.rmtree(work_dir)


def test_apply_results_key_points_only_keeps_sentiment():
    existing = [_row(channel="구글", content="주차장 입구를 막아요", sentiment="부정", method="rating", matched_words="")]
    ws, updated = _run_apply([_sheet_row("주차장 입구를 막아요", "부정", "rating")], existing,
                             [{"id": 0, "key_points": ["주차"], "note": "주차 불편"}])
    h = sheets_writer.HEADER
    assert updated == 1
    assert ws.batch_update_calls == 1
    assert ws.values[1][h.index("key_points")] == "주차"
    assert ws.values[1][h.index("method")] == "rating" and ws.values[1][h.index("sentiment")] == "부정"
    print("PASS: test_apply_results_key_points_only_keeps_sentiment")


def test_apply_results_sentiment_and_key_points():
    existing = [_row(channel="구글", content="상담이 성의 없어요", sentiment="중립", method="lexicon", matched_words="")]
    ws, updated = _run_apply([_sheet_row("상담이 성의 없어요", "중립", "lexicon")], existing,
                             [{"id": 0, "sentiment": "부정", "confirmed": True, "key_points": ["상담 태도"], "note": ""}])
    h = sheets_writer.HEADER
    assert updated == 1 and ws.batch_update_calls == 1
    assert [ws.values[1][h.index(f)] for f in ("sentiment", "method", "key_points")] == ["부정", "claude_review", "상담 태도"]
    print("PASS: test_apply_results_sentiment_and_key_points")


def test_apply_results_key_points_string_is_one_point():
    # 결과 JSON을 손으로 쓰다 리스트 대신 문자열을 넣으면 "주,차"로 쪼개지던 문제 (리뷰 지적)
    existing = [_row(channel="구글", content="주차장 입구를 막아요", sentiment="부정", method="rating", matched_words="")]
    ws, _ = _run_apply([_sheet_row("주차장 입구를 막아요", "부정", "rating")], existing,
                       [{"id": 0, "key_points": "주차", "note": ""}])
    assert ws.values[1][sheets_writer.HEADER.index("key_points")] == "주차", f"실제: {ws.values[1]}"
    print("PASS: test_apply_results_key_points_string_is_one_point")


def test_apply_results_key_points_distinct_rows():
    existing = [_row(channel="구글", content="별로", sentiment="부정", method="rating", matched_words=""),
                _row(channel="구글", content="별로", sentiment="부정", method="rating", matched_words="")]
    ws, updated = _run_apply([_sheet_row("별로", "부정", "rating"), _sheet_row("별로", "부정", "rating")], existing,
                             [{"id": 0, "key_points": ["-"], "note": ""}, {"id": 1, "key_points": ["-"], "note": ""}])
    kp = sheets_writer.HEADER.index("key_points")
    assert updated == 2 and [ws.values[i][kp] for i in (1, 2)] == ["-", "-"], f"실제: {ws.values}"
    print("PASS: test_apply_results_key_points_distinct_rows")



def test_find_candidates_adds_key_point_task_for_positive_rows():
    existing = [
        _row(content="직원분들이 친절해요", sentiment="긍정", method="claude_review"),
        _row(content="좋아요", sentiment="긍정", method="rating"),
        _row(content="그냥 그래요", sentiment="중립", method="claude_review"),
    ]
    candidates = reclassify.find_candidates(existing)
    assert [(c["content"], c["tasks"]) for c in candidates] == [("직원분들이 친절해요", ["key_points"]), ("좋아요", ["key_points"])], f"실제: {candidates}"
    print("PASS: test_find_candidates_adds_key_point_task_for_positive_rows")


def test_review_date_formats():
    at = "2026-10-14T13:00:00+09:00"
    cases = [
        (_dated("구글", "2026-09-03"), "2026-09-03"),
        (_dated("구글", "2026-07"), "2026-07-01"),
        (_dated("카카오맵", "2026.08.20."), "2026-08-20"),
        (_dated("네이버", "9.16.수"), "2026-09-16"),
        (_dated("네이버", "25.3.8.토"), "2025-03-08"),
        (_dated("구글", "5시간 전"), "2026-10-14"),
        (_dated("구글", "2일 전"), "2026-10-12"),
        (_dated("구글", "알수없음"), None),
        (_dated("네이버", ""), None),
    ]
    for review, expected in cases:
        review["collected_at"] = at
        got = reclassify.review_date(review)
        assert (got.isoformat() if got else None) == expected, f"{review['date']!r}: 실제 {got}"
    print("PASS: test_review_date_formats")


def _dated(channel, date, hospital_id="gangnam_jstar"):
    row = _row(hospital_id=hospital_id, channel=channel)
    row["date"] = date
    return row


def test_find_candidates_competitor_only_recent_3_months():
    # 2026-10-14 사용자 결정(B): 경쟁병원은 대시보드 기본 조회 기간(최근 3개월) 리뷰만 재분류한다.
    today = __import__("datetime").date(2026, 10, 14)
    existing = [
        _dated("카카오맵", "2026.07.14.", hospital_id="comp_k1"),   # 경계일(3개월 전 같은 날) 포함
        _dated("카카오맵", "2026.07.13.", hospital_id="comp_k1"),   # 3개월보다 오래됨 → 제외
        _dated("구글", "깨진값", hospital_id="comp_k1"),            # 날짜 모름 → 제외
        _dated("카카오맵", "2020.01.01.", hospital_id="gangnam_jstar"),  # 자사는 기간 무관
    ]
    candidates = reclassify.find_candidates(existing, competitor_ids={"comp_k1"}, today=today)
    assert [(c["hospital_id"], c["id"]) for c in candidates] == [("gangnam_jstar", 0), ("comp_k1", 1)], f"실제: {candidates}"
    print("PASS: test_find_candidates_competitor_only_recent_3_months")


def test_find_candidates_limit_puts_own_hospitals_first():
    today = __import__("datetime").date(2026, 10, 14)
    existing = [
        _dated("카카오맵", "2026.10.01.", hospital_id="comp_k1"),
        _dated("카카오맵", "2026.10.02.", hospital_id="gangnam_jstar"),
        _dated("카카오맵", "2026.10.03.", hospital_id="comp_k2"),
    ]
    candidates = reclassify.find_candidates(existing, competitor_ids={"comp_k1", "comp_k2"}, today=today, limit=2)
    assert [(c["hospital_id"], c["id"]) for c in candidates] == [("gangnam_jstar", 0), ("comp_k1", 1)], f"실제: {candidates}"
    print("PASS: test_find_candidates_limit_puts_own_hospitals_first")


def test_find_candidates_skips_hospitals_no_longer_registered():
    # 2026-10-20: 삭제한 자사 12곳·빠진 경쟁병원 리뷰가 "자사"로 잡혀 대기 4,972건이 됐다
    existing = [_row(hospital_id="own_a", content="자사"), _row(hospital_id="deleted_b", content="삭제됨"),
                _row(hospital_id="comp_k1", channel="구글", content="경쟁")]
    existing[2]["date"] = datetime.date.today().isoformat()
    candidates = reclassify.find_candidates(existing, competitor_ids={"comp_k1"}, own_ids={"own_a"})
    assert [c["content"] for c in candidates] == ["자사", "경쟁"], f"실제: {candidates}"
    print("PASS: test_find_candidates_skips_hospitals_no_longer_registered")



def test_find_candidates_severity_task_for_negative_rows():
    existing = [
        _row(content="주차가 불편", sentiment="부정", method="rating", key_points="주차"),            # 포인트 있음, 심각도 없음
        _row(content="시술 후 염증", sentiment="부정", method="claude_review", key_points="부작용", severity="심각"),  # 다 있음
        _row(content="", sentiment="부정", method="rating"),                                          # 본문 없음
        _row(content="친절해요", sentiment="긍정", method="claude_review", key_points="직원 친절"),     # 긍정은 심각도 없음
    ]
    candidates = reclassify.find_candidates(existing)
    assert [(c["content"], c["tasks"]) for c in candidates] == [("주차가 불편", ["severity"])], f"실제: {candidates}"
    print("PASS: test_find_candidates_severity_task_for_negative_rows")


def test_apply_results_writes_severity_for_negative():
    existing = [_row(channel="구글", content="시술 후 염증이 심해졌어요", sentiment="부정", method="rating", matched_words="")]
    ws, updated = _run_apply([_sheet_row("시술 후 염증이 심해졌어요", "부정", "rating")], existing,
                             [{"id": 0, "key_points": ["시술 부작용"], "severity": "심각", "note": "시술 후 악화 주장"}])
    h = sheets_writer.HEADER
    assert updated == 1 and ws.batch_update_calls == 1
    assert [ws.values[1][h.index(f)] for f in ("key_points", "severity")] == ["시술 부작용", "심각"], f"실제: {ws.values[1]}"
    print("PASS: test_apply_results_writes_severity_for_negative")


def test_apply_results_lexicon_to_negative_with_severity():
    existing = [_row(channel="구글", content="원장이 반말하고 소리를 질렀어요", sentiment="중립", method="lexicon", matched_words="")]
    ws, _ = _run_apply([_sheet_row("원장이 반말하고 소리를 질렀어요", "중립", "lexicon")], existing,
                       [{"id": 0, "sentiment": "부정", "confirmed": True, "key_points": ["의료진 태도"], "severity": "심각", "note": ""}])
    h = sheets_writer.HEADER
    assert [ws.values[1][h.index(f)] for f in ("sentiment", "severity")] == ["부정", "심각"], f"실제: {ws.values[1]}"
    print("PASS: test_apply_results_lexicon_to_negative_with_severity")


def test_apply_results_ignores_invalid_or_non_negative_severity():
    existing = [
        _row(channel="구글", content="대기가 길어요", sentiment="부정", method="rating", matched_words="", key_points="대기 시간"),
        _row(channel="구글", content="친절하고 좋아요", sentiment="중립", method="lexicon", matched_words=""),
    ]
    ws, _ = _run_apply([_sheet_row("대기가 길어요", "부정", "rating", key_points="대기 시간"),
                        _sheet_row("친절하고 좋아요", "중립", "lexicon")], existing,
                       [{"id": 0, "severity": "높음", "note": "잘못된 값"},
                        {"id": 1, "sentiment": "긍정", "confirmed": True, "key_points": ["직원 친절"], "severity": "일반", "note": ""}])
    sv = sheets_writer.HEADER.index("severity")
    assert ws.values[1][sv] == "" and ws.values[2][sv] == "", f"실제: {ws.values}"
    assert ws.values[2][sheets_writer.HEADER.index("sentiment")] == "긍정"  # 다른 필드는 정상 반영
    print("PASS: test_apply_results_ignores_invalid_or_non_negative_severity")


def test_negative_without_severity_comes_back_as_severity_candidate():
    # lexicon → 부정으로 바뀌었는데 severity를 빠뜨린 행은 다음 export에 severity 작업으로 다시 나온다
    existing = [_row(channel="구글", content="상담이 성의 없어요", sentiment="중립", method="lexicon", matched_words="")]
    ws, _ = _run_apply([_sheet_row("상담이 성의 없어요", "중립", "lexicon")], existing,
                       [{"id": 0, "sentiment": "부정", "confirmed": True, "key_points": ["상담 태도"], "note": ""}])
    after = sheets_writer.read_existing_reviews(ws)
    assert [c["tasks"] for c in reclassify.find_candidates(after)] == [["severity"]], f"실제: {after}"
    print("PASS: test_negative_without_severity_comes_back_as_severity_candidate")


def test_apply_results_matches_request_exported_before_severity_task():
    # 최종 리뷰 I-2: severity 작업이 생기기 전에 export한 요청 파일(tasks=["key_points"])도 반영돼야 한다
    existing = [_row(channel="구글", content="주차장이 좁아요", sentiment="부정", method="rating", matched_words="")]
    work_dir = tempfile.mkdtemp()
    try:
        with open(os.path.join(work_dir, reclassify.REQUEST_FILENAME), "w", encoding="utf-8") as f:
            json.dump({"generated_at": "2026-10-19T10:00:00+09:00", "existing_key_points": {}, "candidates": [
                {"id": 0, "hospital_id": "gangnam_jstar", "channel": "구글", "content": "주차장이 좁아요",
                 "current_sentiment": "부정", "current_score": "", "current_matched_words": [], "tasks": ["key_points"]}]},
                f, ensure_ascii=False)
        with open(os.path.join(work_dir, reclassify.RESULT_FILENAME), "w", encoding="utf-8") as f:
            json.dump({"results": [{"id": 0, "key_points": ["주차"], "note": ""}]}, f, ensure_ascii=False)
        ws = FakeWorksheet(initial_values=[sheets_writer.HEADER, _sheet_row("주차장이 좁아요", "부정", "rating", hospital_id="gangnam_jstar")])
        updated = reclassify.apply_results(ws, existing, work_dir)
    finally:
        shutil.rmtree(work_dir)
    assert updated == 1 and ws.values[1][sheets_writer.HEADER.index("key_points")] == "주차", f"실제: {ws.values}"
    print("PASS: test_apply_results_matches_request_exported_before_severity_task")


if __name__ == "__main__":
    test_find_candidates_skips_hospitals_no_longer_registered()
    test_find_candidates_filters_lexicon_only()
    test_find_candidates_splits_matched_words_into_list()
    test_find_candidates_empty_matched_words_becomes_empty_list()
    test_export_for_claude_writes_json_and_returns_path()
    test_export_for_claude_returns_none_and_writes_no_file_when_no_candidates()
    test_apply_results_updates_matching_row_and_logs()
    test_apply_results_skips_when_row_not_found_in_existing_reviews()
    test_apply_results_maps_duplicate_contents_to_distinct_lexicon_rows()
    test_apply_results_does_not_match_other_hospital_with_same_content()
    test_apply_results_raises_when_request_file_missing()
    test_find_candidates_adds_key_point_task_for_negative_rows()
    test_find_candidates_skips_rows_with_key_points()
    test_export_includes_existing_key_points()
    test_apply_results_key_points_only_keeps_sentiment()
    test_apply_results_sentiment_and_key_points()
    test_apply_results_key_points_distinct_rows()
    test_apply_results_key_points_string_is_one_point()
    test_find_candidates_adds_key_point_task_for_positive_rows()
    test_review_date_formats()
    test_find_candidates_competitor_only_recent_3_months()
    test_find_candidates_limit_puts_own_hospitals_first()
    test_find_candidates_severity_task_for_negative_rows()
    test_apply_results_writes_severity_for_negative()
    test_apply_results_lexicon_to_negative_with_severity()
    test_apply_results_ignores_invalid_or_non_negative_severity()
    test_negative_without_severity_comes_back_as_severity_candidate()
    test_apply_results_matches_request_exported_before_severity_task()
