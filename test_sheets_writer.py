import re
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import sheets_writer


class FakeWorksheet:
    """gspread Worksheet를 흉내낸 가짜 객체. values[0]이 헤더 행이다."""

    def __init__(self, initial_values=None):
        self.values = [list(row) for row in (initial_values or [])]
        self.batch_update_calls = 0

    def get_all_values(self):
        return self.values

    def append_row(self, row):
        self.values.append(list(row))

    def update(self, range_name, values):
        if range_name == "A1":
            if not self.values:
                self.values.append(list(values[0]))
            else:
                self.values[0] = list(values[0])
            return
        # 단일 셀 범위(예: "H2") 갱신 — update_sentiment 테스트용.
        self._set_cell(range_name, values)

    def batch_update(self, data, value_input_option=None):
        # gspread의 batch_update: [{"range": ..., "values": [[...]]}, ...]를
        # 한 번의 API 호출로 반영한다. 호출 횟수를 세어 "정말 한 번에 묶였는지"
        # 테스트에서 검증할 수 있게 한다.
        self.batch_update_calls += 1
        for item in data:
            self._set_cell(item["range"], item["values"])

    def _set_cell(self, range_name, values):
        match = re.match(r"^([A-Z]+)(\d+)$", range_name)
        assert match, f"이 가짜 객체는 A1 또는 단일 셀 범위만 지원함: {range_name!r}"
        col_letters, row_str = match.groups()
        col_index = 0
        for ch in col_letters:
            col_index = col_index * 26 + (ord(ch) - ord("A") + 1)
        row = self.values[int(row_str) - 1]
        while len(row) < col_index:  # 실제 시트처럼 뒤쪽 빈 칸에도 쓸 수 있게 늘린다
            row.append("")
        row[col_index - 1] = values[0][0]

    def append_rows(self, rows, value_input_option=None):
        for row in rows:
            self.values.append(list(row))

    def get_all_records(self):
        if not self.values:
            return []
        header = self.values[0]
        return [dict(zip(header, row)) for row in self.values[1:]]


def test_ensure_header_writes_header_when_sheet_empty():
    ws = FakeWorksheet()
    sheets_writer.ensure_header(ws)
    assert ws.values == [sheets_writer.HEADER], f"실제: {ws.values}"
    print("PASS: test_ensure_header_writes_header_when_sheet_empty")


def test_ensure_header_writes_header_when_first_row_is_blank():
    # 실제 GitHub Actions 실행에서 확인된 사례: 새로 만든 Google Sheet는
    # get_all_values()가 완전히 빈 리스트([])가 아니라 빈 행 하나([[]])를
    # 반환하기도 한다. 이 경우도 "헤더 없음"으로 취급해 A1에 헤더를 써야 한다.
    ws = FakeWorksheet(initial_values=[[]])
    sheets_writer.ensure_header(ws)
    assert ws.values == [sheets_writer.HEADER], f"실제: {ws.values}"
    print("PASS: test_ensure_header_writes_header_when_first_row_is_blank")


def test_ensure_header_noop_when_header_already_correct():
    ws = FakeWorksheet(initial_values=[sheets_writer.HEADER, ["gangnam_jstar", "네이버", "홍길동", "", "2026-08-01", "좋아요", False, "긍정", True, 2, "친절", "lexicon", "2026-09-16T00:00:00"]])
    sheets_writer.ensure_header(ws)
    assert len(ws.values) == 2, "헤더가 중복 추가되면 안 됨"
    print("PASS: test_ensure_header_noop_when_header_already_correct")


def test_ensure_header_raises_when_mismatched():
    ws = FakeWorksheet(initial_values=[["엉뚱한", "헤더"]])
    raised = False
    try:
        sheets_writer.ensure_header(ws)
    except ValueError:
        raised = True
    assert raised, "헤더가 예상과 다르면 조용히 넘어가지 말고 예외를 던져야 함"
    print("PASS: test_ensure_header_raises_when_mismatched")


def test_review_to_row():
    review = {"channel": "네이버", "author": "홍길동", "rating": None, "date": "2026-08-01", "content": "친절하고 좋았어요", "has_reply": False}
    classification = {"sentiment": "긍정", "confirmed": True, "score": 2, "matched_words": ["친절"], "method": "lexicon"}
    row = sheets_writer.review_to_row("gangnam_jstar", review, classification, "2026-09-16T00:00:00")
    assert row == [
        "gangnam_jstar", "네이버", "홍길동", "", "2026-08-01", "친절하고 좋았어요", False,
        "긍정", True, 2, "친절", "lexicon", "2026-09-16T00:00:00", "", "",
    ], f"실제: {row}"
    print("PASS: test_review_to_row")


def test_review_to_row_with_rating_and_no_matched_words():
    review = {"channel": "카카오맵", "author": "이영희", "rating": 1, "date": "2026.07.15.", "content": "별로였어요", "has_reply": True}
    classification = {"sentiment": "부정", "confirmed": True, "score": None, "matched_words": [], "method": "rating"}
    row = sheets_writer.review_to_row("gangnam_jstar", review, classification, "2026-09-16T00:00:00")
    assert row == [
        "gangnam_jstar", "카카오맵", "이영희", 1, "2026.07.15.", "별로였어요", True,
        "부정", True, "", "", "rating", "2026-09-16T00:00:00", "", "",
    ], f"실제: {row}"
    print("PASS: test_review_to_row_with_rating_and_no_matched_words")


def test_append_reviews_writes_rows_and_returns_count():
    ws = FakeWorksheet(initial_values=[sheets_writer.HEADER])
    review = {"channel": "네이버", "author": "홍길동", "rating": None, "date": "2026-08-01", "content": "좋아요", "has_reply": False}
    classification = {"sentiment": "긍정", "confirmed": False, "score": 0, "matched_words": [], "method": "lexicon"}
    count = sheets_writer.append_reviews(ws, "gangnam_jstar", [(review, classification)], "2026-09-16T00:00:00")
    assert count == 1
    assert len(ws.values) == 2, f"실제: {ws.values}"
    print("PASS: test_append_reviews_writes_rows_and_returns_count")


def test_append_reviews_noop_when_empty_list():
    ws = FakeWorksheet(initial_values=[sheets_writer.HEADER])
    count = sheets_writer.append_reviews(ws, "gangnam_jstar", [], "2026-09-16T00:00:00")
    assert count == 0
    assert len(ws.values) == 1, "빈 목록이면 시트에 아무것도 추가되면 안 됨"
    print("PASS: test_append_reviews_noop_when_empty_list")


def test_read_existing_reviews_returns_records():
    ws = FakeWorksheet(initial_values=[
        sheets_writer.HEADER,
        ["gangnam_jstar", "네이버", "홍길동", "", "2026-08-01", "좋아요", False, "긍정", True, 2, "친절", "lexicon", "2026-09-16T00:00:00"],
    ])
    records = sheets_writer.read_existing_reviews(ws)
    assert len(records) == 1
    assert records[0]["channel"] == "네이버"
    assert records[0]["content"] == "좋아요"
    assert records[0]["hospital_id"] == "gangnam_jstar"
    print("PASS: test_read_existing_reviews_returns_records")


def test_update_sentiment_updates_only_sentiment_confirmed_method_columns():
    ws = FakeWorksheet(initial_values=[
        sheets_writer.HEADER,
        ["gangnam_jstar", "네이버", "홍길동", "", "2026-08-01", "염증 때문에 병원 갔는데 친절해서 좋았어요", False, "부정", True, -4, "염증,스트레스,친절", "lexicon", "2026-09-16T00:00:00"],
    ])
    sheets_writer.update_sentiment(ws, 2, {"sentiment": "긍정", "confirmed": True, "method": "claude_review"})
    assert ws.values[1] == [
        "gangnam_jstar", "네이버", "홍길동", "", "2026-08-01", "염증 때문에 병원 갔는데 친절해서 좋았어요", False,
        "긍정", True, -4, "염증,스트레스,친절", "claude_review", "2026-09-16T00:00:00",
    ], f"실제: {ws.values[1]}"
    print("PASS: test_update_sentiment_updates_only_sentiment_confirmed_method_columns")


def test_batch_update_sentiments_updates_multiple_rows_in_a_single_api_call():
    ws = FakeWorksheet(initial_values=[
        sheets_writer.HEADER,
        ["h1", "네이버", "a", "", "2026-08-01", "리뷰1", False, "부정", True, -2, "져서", "lexicon", "t"],
        ["h1", "네이버", "b", "", "2026-08-01", "리뷰2", False, "중립", True, 0, "", "lexicon", "t"],
    ])
    sheets_writer.batch_update_sentiments(ws, [
        (2, {"sentiment": "긍정", "confirmed": True, "method": "claude_review"}),
        (3, {"sentiment": "긍정", "confirmed": True, "method": "claude_review"}),
    ])
    assert ws.batch_update_calls == 1, f"85행을 개별 update()로 갱신하면 Sheets API 쓰기 quota(429)를 초과한다 — 실제: {ws.batch_update_calls}회 호출"
    assert ws.values[1][sheets_writer.HEADER.index("sentiment")] == "긍정"
    assert ws.values[1][sheets_writer.HEADER.index("method")] == "claude_review"
    assert ws.values[2][sheets_writer.HEADER.index("sentiment")] == "긍정"
    print("PASS: test_batch_update_sentiments_updates_multiple_rows_in_a_single_api_call")


def test_batch_update_sentiments_noop_when_no_updates():
    ws = FakeWorksheet(initial_values=[sheets_writer.HEADER])
    sheets_writer.batch_update_sentiments(ws, [])
    assert ws.batch_update_calls == 0, "갱신할 게 없으면 API 호출 자체를 하지 말아야 함"
    print("PASS: test_batch_update_sentiments_noop_when_no_updates")


def test_review_to_row_has_reply_none_is_blank():
    classification = {"sentiment": "긍정", "confirmed": True, "score": 2, "matched_words": [], "method": "lexicon"}
    col = sheets_writer.HEADER.index("has_reply")
    base = {"channel": "네이버", "author": "", "rating": None, "date": "9.16.수", "content": "좋아요"}
    assert sheets_writer.review_to_row("h_a", {**base, "has_reply": None}, classification, "t")[col] == ""
    assert sheets_writer.review_to_row("h_a", {**base, "has_reply": True}, classification, "t")[col] is True
    assert sheets_writer.review_to_row("h_a", base, classification, "t")[col] is False
    print("PASS: test_review_to_row_has_reply_none_is_blank")



def test_ensure_header_extends_missing_trailing_columns():
    # 구버전 시트(key_points 컬럼 추가 전) — 앞부분이 정확히 같으면 빠진 뒤 컬럼만 붙인다
    ws = FakeWorksheet(initial_values=[sheets_writer.HEADER[:-1], ["h_a"] + [""] * 12])
    sheets_writer.ensure_header(ws)
    assert ws.values[0] == sheets_writer.HEADER, f"실제: {ws.values[0]}"
    assert ws.batch_update_calls == 1
    assert len(ws.values) == 2
    print("PASS: test_ensure_header_extends_missing_trailing_columns")


def test_batch_update_fields_writes_arbitrary_columns_in_one_call():
    width = len(sheets_writer.HEADER)
    ws = FakeWorksheet(initial_values=[sheets_writer.HEADER, ["a"] * width, ["b"] * width])
    sheets_writer.batch_update_fields(ws, [(2, {"key_points": "주차,가격"}), (3, {"sentiment": "부정", "key_points": "-"})])
    kp = sheets_writer.HEADER.index("key_points")
    st = sheets_writer.HEADER.index("sentiment")
    assert ws.batch_update_calls == 1
    assert ws.values[1][kp] == "주차,가격" and ws.values[1][st] == "a"
    assert ws.values[2][kp] == "-" and ws.values[2][st] == "부정"
    print("PASS: test_batch_update_fields_writes_arbitrary_columns_in_one_call")


def test_review_to_row_key_points_blank():
    classification = {"sentiment": "부정", "confirmed": True, "score": None, "matched_words": [], "method": "rating"}
    row = sheets_writer.review_to_row("h_a", {"channel": "구글", "author": "", "rating": 1, "date": "", "content": "별로"}, classification, "t")
    assert len(row) == len(sheets_writer.HEADER)
    assert row[sheets_writer.HEADER.index("key_points")] == ""
    print("PASS: test_review_to_row_key_points_blank")


def test_header_ends_with_severity_and_row_blank():
    assert sheets_writer.HEADER[-2:] == ["key_points", "severity"], f"실제: {sheets_writer.HEADER}"
    classification = {"sentiment": "부정", "confirmed": True, "score": None, "matched_words": [], "method": "rating"}
    row = sheets_writer.review_to_row("h_a", {"channel": "구글", "author": "", "rating": 1, "date": "", "content": "별로"}, classification, "t")
    assert len(row) == len(sheets_writer.HEADER)
    assert row[sheets_writer.HEADER.index("severity")] == ""
    print("PASS: test_header_ends_with_severity_and_row_blank")


def test_ensure_header_adds_severity_to_key_points_era_sheet():
    # key_points까지만 있는 현재 운영 시트 → severity만 뒤에 붙는다
    old = sheets_writer.HEADER[:sheets_writer.HEADER.index("key_points") + 1]
    ws = FakeWorksheet(initial_values=[old, ["h_a"] + [""] * (len(old) - 1)])
    sheets_writer.ensure_header(ws)
    assert ws.values[0] == sheets_writer.HEADER, f"실제: {ws.values[0]}"
    print("PASS: test_ensure_header_adds_severity_to_key_points_era_sheet")



def test_ensure_header_accepts_longer_sheet_header_with_same_prefix():
    # 최종 리뷰 I-1: 새 코드가 시트에 컬럼을 먼저 붙인 뒤 옛 코드(Actions)가 돌아도 수집이 멈추지 않게,
    # 시트 헤더가 더 길어도 앞부분이 HEADER와 같으면 통과한다(시트는 건드리지 않음).
    longer = sheets_writer.HEADER + ["future_col"]
    ws = FakeWorksheet(initial_values=[list(longer)])
    sheets_writer.ensure_header(ws)
    assert ws.values[0] == longer, f"실제: {ws.values[0]}"
    print("PASS: test_ensure_header_accepts_longer_sheet_header_with_same_prefix")


class _FakeAPIError(Exception):
    def __init__(self, code):
        super().__init__(f"[{code}]: The operation was aborted.")
        self.code = code


class FlakyWorksheet(FakeWorksheet):
    """append_rows가 앞쪽 몇 번 일시 오류를 낸다. written_anyway=True면 실제로는 써 놓고 오류를 낸다
    (응답만 잃어버린 경우 — 다시 쓰면 같은 행이 두 번 들어간다)."""

    def __init__(self, initial_values, failures, code=409, written_anyway=False):
        super().__init__(initial_values)
        self.failures, self.code, self.written_anyway = failures, code, written_anyway
        self.append_calls = 0

    def append_rows(self, rows, value_input_option=None):
        self.append_calls += 1
        if self.failures > 0:
            self.failures -= 1
            if self.written_anyway:
                super().append_rows(rows)
            raise _FakeAPIError(self.code)
        super().append_rows(rows)


def _two_reviews():
    c = {"sentiment": "긍정", "confirmed": False, "score": 0, "matched_words": [], "method": "lexicon"}
    return [({"channel": "네이버", "author": "", "date": "", "content": "좋아요"}, c),
            ({"channel": "구글", "author": "", "date": "", "content": "친절  해요"}, c)]


def _with_no_sleep(fn):
    waits = []
    orig = sheets_writer._sleep
    sheets_writer._sleep = waits.append
    try:
        return fn(), waits
    finally:
        sheets_writer._sleep = orig


def test_append_retries_transient_error_when_rows_not_written():
    # 2026-10-22 19:02 Actions: append_rows가 409 "The operation was aborted"로 실패 → 재시도 없이 수집 전체 중단
    ws = FlakyWorksheet([sheets_writer.HEADER], failures=1)
    count, waits = _with_no_sleep(lambda: sheets_writer.append_reviews(ws, "h_a", _two_reviews(), "T1"))
    assert count == 2 and ws.append_calls == 2 and len(waits) == 1
    assert len(ws.values) == 3, f"실제: {ws.values}"
    print("PASS: test_append_retries_transient_error_when_rows_not_written")


def test_append_does_not_duplicate_when_failed_call_actually_wrote():
    # 오류가 났어도 실제로 써졌으면 다시 쓰지 않는다(2026-10-23 사용자: 같은 행 두 번은 안 됨)
    ws = FlakyWorksheet([sheets_writer.HEADER], failures=1, written_anyway=True)
    count, _ = _with_no_sleep(lambda: sheets_writer.append_reviews(ws, "h_a", _two_reviews(), "T1"))
    assert count == 2 and ws.append_calls == 1
    assert len(ws.values) == 3, f"같은 행이 두 번 들어감: {ws.values}"
    print("PASS: test_append_does_not_duplicate_when_failed_call_actually_wrote")


def test_append_rewrites_only_rows_from_this_run():
    # 같은 본문이 이전 수집(collected_at 다름)에 있어도 이번 행은 써야 한다 — 이번 수집 시각까지 같아야 "이미 씀"
    old = sheets_writer.review_to_row("h_a", _two_reviews()[0][0], _two_reviews()[0][1], "T0")
    ws = FlakyWorksheet([sheets_writer.HEADER, old], failures=1)
    count, _ = _with_no_sleep(lambda: sheets_writer.append_reviews(ws, "h_a", _two_reviews(), "T1"))
    assert count == 2 and len(ws.values) == 4, f"실제: {ws.values}"
    print("PASS: test_append_rewrites_only_rows_from_this_run")


def test_append_raises_immediately_on_non_transient_error():
    ws = FlakyWorksheet([sheets_writer.HEADER], failures=1, code=400)
    try:
        _with_no_sleep(lambda: sheets_writer.append_reviews(ws, "h_a", _two_reviews(), "T1"))
        assert False, "400은 재시도하지 않고 바로 예외"
    except _FakeAPIError:
        pass
    assert ws.append_calls == 1
    print("PASS: test_append_raises_immediately_on_non_transient_error")


def test_append_gives_up_after_max_attempts():
    ws = FlakyWorksheet([sheets_writer.HEADER], failures=99, code=503)
    try:
        _with_no_sleep(lambda: sheets_writer.append_reviews(ws, "h_a", _two_reviews(), "T1"))
        assert False, "끝내 실패하면 예외"
    except _FakeAPIError:
        pass
    assert ws.append_calls == sheets_writer.APPEND_MAX_ATTEMPTS and len(ws.values) == 1
    print("PASS: test_append_gives_up_after_max_attempts")


if __name__ == "__main__":
    test_append_retries_transient_error_when_rows_not_written()
    test_append_does_not_duplicate_when_failed_call_actually_wrote()
    test_append_rewrites_only_rows_from_this_run()
    test_append_raises_immediately_on_non_transient_error()
    test_append_gives_up_after_max_attempts()
    test_ensure_header_writes_header_when_sheet_empty()
    test_ensure_header_writes_header_when_first_row_is_blank()
    test_ensure_header_noop_when_header_already_correct()
    test_ensure_header_raises_when_mismatched()
    test_review_to_row()
    test_review_to_row_with_rating_and_no_matched_words()
    test_append_reviews_writes_rows_and_returns_count()
    test_append_reviews_noop_when_empty_list()
    test_read_existing_reviews_returns_records()
    test_update_sentiment_updates_only_sentiment_confirmed_method_columns()
    test_batch_update_sentiments_updates_multiple_rows_in_a_single_api_call()
    test_batch_update_sentiments_noop_when_no_updates()
    test_review_to_row_has_reply_none_is_blank()
    test_ensure_header_extends_missing_trailing_columns()
    test_batch_update_fields_writes_arbitrary_columns_in_one_call()
    test_review_to_row_key_points_blank()
    test_header_ends_with_severity_and_row_blank()
    test_ensure_header_adds_severity_to_key_points_era_sheet()
    test_ensure_header_accepts_longer_sheet_header_with_same_prefix()
