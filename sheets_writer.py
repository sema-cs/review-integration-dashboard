"""Google Sheets 리더/라이터.

서비스 계정 인증과 실제 Sheets API 호출(connect())은 유닛테스트 대상이 아니다
(수동으로 실제 계정으로 검증한다). 행 변환·헤더 검증·append 로직은 gspread
워크시트를 흉내낸 가짜 객체로 테스트한다.
"""

import json
import time
from collections import Counter

HEADER = [
    "hospital_id", "channel", "author", "rating", "date", "content", "has_reply",
    "sentiment", "confirmed", "score", "matched_words", "method", "collected_at",
    "key_points",  # 부정 리뷰의 주요 불만 포인트(Claude 재분류 단계에서 기록, 2026-09-29 추가)
    "severity",  # 부정 리뷰 심각도 "심각"|"일반"(Claude 재분류 단계에서 기록, 2026-10-20 추가)
]


def _row_is_blank(row):
    return all(not str(cell).strip() for cell in row)


def ensure_header(worksheet):
    """시트가 비어 있으면 A1에 헤더를 쓰고, 이미 있으면 정확히 일치하는지만
    확인한다. 다르면 조용히 덮어쓰지 않고 예외를 던진다 — 잘못된 시트에 계속
    쓰는 사고를 막는다.

    새로 만든 Google Sheet는 get_all_values()가 완전히 빈 리스트가 아니라
    빈 행 하나([[]])를 반환하기도 한다(실제 GitHub Actions 실행에서 확인됨) —
    그래서 "리스트가 비었는지"가 아니라 "첫 행이 비었는지"로 판단한다."""
    values = worksheet.get_all_values()
    if not values or _row_is_blank(values[0]):
        worksheet.update("A1", [HEADER])
        return
    current = list(values[0])
    if current == HEADER:
        return
    # 뒤에 컬럼을 새로 붙인 경우(예: key_points): 앞부분이 정확히 같을 때만 빠진 헤더를
    # 채운다 — 코드 배포와 시트 헤더 변경의 순서를 맞추지 않아도 되게 한다.
    if len(current) < len(HEADER) and current == HEADER[:len(current)]:
        worksheet.batch_update([
            {"range": f"{_col_letter(i)}1", "values": [[HEADER[i]]]}
            for i in range(len(current), len(HEADER))
        ])
        return
    # 반대로 시트가 더 길면(새 코드가 먼저 컬럼을 붙였고 이 코드는 아직 옛 버전) 앞부분만 맞으면 통과한다.
    # append는 HEADER 길이만큼만 쓰므로 뒤 컬럼은 빈 값으로 남는다(2026-10-20 리뷰 지적: 수집 중단 방지).
    if len(current) > len(HEADER) and current[:len(HEADER)] == HEADER:
        return
    raise ValueError(f"시트 헤더가 예상과 다릅니다: {values[0]}")


def review_to_row(hospital_id, review, classification, collected_at):
    rating = review.get("rating")
    score = classification.get("score")
    has_reply = review.get("has_reply", False)  # None = 모름(확장 엑셀엔 답글 정보 없음)
    return [
        hospital_id,
        review.get("channel"),
        review.get("author"),
        rating if rating is not None else "",
        review.get("date"),
        review.get("content"),
        "" if has_reply is None else bool(has_reply),
        classification["sentiment"],
        classification["confirmed"],
        score if score is not None else "",
        ",".join(classification.get("matched_words") or []),
        classification["method"],
        collected_at,
        "",  # key_points — 재분류 단계에서 채운다
        "",  # severity — 재분류 단계에서 채운다(부정 리뷰만)
    ]


def append_reviews(worksheet, hospital_id, reviews_with_classification, collected_at):
    """reviews_with_classification: [(review_dict, classification_dict), ...]
    반환값: 실제로 추가된 행 수."""
    rows = [review_to_row(hospital_id, review, classification, collected_at) for review, classification in reviews_with_classification]
    if rows:
        _append_rows_once(worksheet, rows)
    return len(rows)


# 2026-10-22 19:02 Actions에서 append_rows가 409 "The operation was aborted"로 한 번 실패해 수집 전체가 멈췄다.
# 일시 오류면 기다렸다가 다시 쓰되, 오류가 났어도 실제로는 써졌을 수 있으므로 다시 쓰기 전에 시트를 읽어
# 이번 수집에서 이미 들어간 행은 빼고 쓴다(2026-10-23 사용자: 같은 행이 두 번 들어가면 안 됨).
APPEND_MAX_ATTEMPTS = 3
APPEND_RETRY_WAITS = (10, 30)  # n번째 실패 뒤 기다리는 초
_TRANSIENT_STATUS = {409, 429, 500, 502, 503, 504}
_sleep = time.sleep  # 테스트에서 바꿔 끼운다


def _status_code(e):
    code = getattr(e, "code", None)  # gspread APIError
    if isinstance(code, int):
        return code
    return getattr(getattr(e, "response", None), "status_code", None)


def _is_transient(e):
    # requests의 연결·타임아웃 오류는 OSError 하위 클래스다
    return _status_code(e) in _TRANSIENT_STATUS or isinstance(e, OSError)


def _row_key(row):
    """이번 수집의 같은 행인지 가리는 키. collected_at이 실행마다 달라 예전 수집의 같은 본문과 섞이지 않는다."""
    pick = [HEADER.index(f) for f in ("hospital_id", "channel", "content", "collected_at")]
    return tuple(" ".join(str(row[i] if i < len(row) else "").split()) for i in pick)


def _rows_not_in_sheet(worksheet, rows):
    present = Counter(_row_key(r) for r in worksheet.get_all_values()[1:])
    missing = []
    for row in rows:
        key = _row_key(row)
        if present[key] > 0:
            present[key] -= 1
        else:
            missing.append(row)
    return missing


def _append_rows_once(worksheet, rows):
    pending = rows
    for attempt in range(APPEND_MAX_ATTEMPTS):
        try:
            if attempt:
                pending = _rows_not_in_sheet(worksheet, pending)
                if not pending:
                    print(f"  시트 확인: 앞선 쓰기가 실제로는 반영돼 있어 다시 쓰지 않음({len(rows)}행)")
                    return
            worksheet.append_rows(pending, value_input_option="RAW")
            return
        except Exception as e:
            if attempt == APPEND_MAX_ATTEMPTS - 1 or not _is_transient(e):
                raise
            wait = APPEND_RETRY_WAITS[attempt]
            print(f"  시트 쓰기 일시 오류({e}) — {wait}초 뒤 반영 여부를 확인하고 다시 시도({attempt + 2}/{APPEND_MAX_ATTEMPTS})")
            _sleep(wait)


def _col_letter(index):
    """0-based 컬럼 인덱스를 시트 열 문자로 변환한다 (0->A, 7->H ...)."""
    letters = ""
    n = index + 1
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def update_sentiment(worksheet, row_number, classification):
    """row_number: 1-based 시트 행 번호(헤더=1행).
    sentiment/confirmed/method 컬럼만 갱신한다. score/matched_words는 원래
    규칙기반 판단의 증거로 그대로 남긴다(재분류 설계문서 참고)."""
    for field in ("sentiment", "confirmed", "method"):
        col = _col_letter(HEADER.index(field))
        worksheet.update(f"{col}{row_number}", [[classification[field]]])


def batch_update_sentiments(worksheet, updates):
    """updates: [(row_number, classification), ...]
    모든 행을 단 한 번의 Sheets API 호출(batch_update)로 갱신한다. 행마다
    update_sentiment()으로 개별 호출하면 행 수 x 3(컬럼)만큼 API를 호출하게 되어,
    실제로 85행 재분류 시 'Write requests per minute' 429 quota 에러가 발생했다
    (2026-09-22 실측)."""
    batch_update_fields(worksheet, [
        (row_number, {field: classification[field] for field in ("sentiment", "confirmed", "method")})
        for row_number, classification in updates
    ])


def batch_update_fields(worksheet, updates):
    """updates: [(row_number, {field: value, ...}), ...] — 모든 셀을 batch_update 한 번으로 쓴다."""
    data = [
        {"range": f"{_col_letter(HEADER.index(field))}{row_number}", "values": [[value]]}
        for row_number, fields in updates
        for field, value in fields.items()
    ]
    if data:
        worksheet.batch_update(data)


def read_existing_reviews(worksheet):
    """헤더를 키로 하는 dict 목록을 반환한다(hospital_id/channel/content 포함)."""
    return worksheet.get_all_records()


def connect(sheet_id, credentials_json):
    """서비스 계정 JSON(문자열)으로 인증해 첫 번째 워크시트를 반환한다.
    실제 네트워크 호출이라 유닛테스트 대상이 아니다."""
    import gspread
    from google.oauth2.service_account import Credentials

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(json.loads(credentials_json), scopes=scopes)
    client = gspread.authorize(creds)
    return client.open_by_key(sheet_id).sheet1
