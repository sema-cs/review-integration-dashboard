import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from unittest.mock import patch, MagicMock
from collectors.naver import _extract_place_id, _via_graphql, _via_embedded_state, collect_full, NaverBlockedError, NaverUrlFormatError


def test_extract_place_id_hospital_segment():
    url = "https://m.place.naver.com/hospital/31605858/review/visitor"
    assert _extract_place_id(url) == "31605858", f"실제: {_extract_place_id(url)}"
    print("PASS: test_extract_place_id_hospital_segment")


def test_extract_place_id_no_digits():
    url = "https://m.place.naver.com/place/review/visitor"
    assert _extract_place_id(url) is None
    print("PASS: test_extract_place_id_no_digits")


def _fake_response(status_code=200, text="{}", json_value=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    if json_value is not None:
        resp.json.return_value = json_value
    return resp


def test_via_graphql_success():
    json_value = [{
        "data": {
            "visitorReviews": {
                "items": [{
                    "id": "r1",
                    "rating": None,
                    "body": "친절하고 좋았어요",
                    "created": "2026-08-01",
                    "visitCount": 1,
                    "author": {"nickname": "홍길동"},
                    "reply": {"body": "감사합니다"},
                }],
                "total": 1,
            }
        }
    }]
    fake = _fake_response(json_value=json_value)
    with patch("collectors.naver.requests.post", return_value=fake):
        reviews, err = _via_graphql("31605858")
    assert err is None, f"에러 없어야: {err}"
    assert len(reviews) == 1
    r = reviews[0]
    assert r["channel"] == "네이버"
    assert r["author"] == "홍길동"
    assert r["content"] == "친절하고 좋았어요"
    assert r["has_reply"] is True
    print("PASS: test_via_graphql_success")


def test_via_graphql_blocked():
    fake = _fake_response(status_code=429, text="captcha")
    with patch("collectors.naver.requests.post", return_value=fake):
        reviews, err = _via_graphql("31605858")
    assert reviews is None
    assert "429" in err or "차단" in err, f"실제: {err}"
    print("PASS: test_via_graphql_blocked")


def test_collect_full_raises_when_place_id_missing():
    hospital = {"channels": {"naver_place_url": "https://m.place.naver.com/place/review/visitor"}}
    raised = False
    try:
        collect_full(hospital)
    except NaverBlockedError:
        raised = True
    assert raised, "businessId 추출 실패 시 NaverBlockedError가 발생해야 함"
    print("PASS: test_collect_full_raises_when_place_id_missing")


def test_collect_full_success_via_graphql():
    hospital = {"channels": {"naver_place_url": "https://m.place.naver.com/hospital/31605858/review/visitor"}}
    fake = _fake_response(json_value=[{"data": {"visitorReviews": {"items": [], "total": 0}}}])
    with patch("collectors.naver.polite_sleep"), patch("collectors.naver.requests.post", return_value=fake):
        reviews = collect_full(hospital)
    assert reviews == [], f"실제: {reviews}"
    print("PASS: test_collect_full_success_via_graphql")


def test_collect_full_rejects_booking_and_short_urls():
    # 예약 URL의 bizes 번호로 GraphQL을 부르면 오류 없이 0건이 나와 방치된다 — 호출 전에 막아야 함
    for url in ("https://booking.naver.com/booking/13/bizes/1234", "https://naver.me/abcd"):
        hospital = {"hospital_id": "comp_test", "channels": {"naver_place_url": url}}
        with patch("collectors.naver.requests.post") as mock_post:
            try:
                collect_full(hospital)
                raised = False
            except NaverUrlFormatError:
                raised = True
        assert raised, f"{url}에서 NaverUrlFormatError가 발생해야 함"
        mock_post.assert_not_called()
    print("PASS: test_collect_full_rejects_booking_and_short_urls")


def test_collect_full_warns_with_graphql_error_when_falling_back(capsys=None):
    # 2026-10-12 더안청담의원: GraphQL이 실패해 예비 경로(HTML, 10건)로 수집됐는데, 예비 경로가
    # 성공하면 GraphQL 실패 이유가 어디에도 남지 않아 원인을 확인할 수 없었다.
    import io, contextlib
    hospital = {"hospital_id": "test_clinic",
                "channels": {"naver_place_url": "https://m.place.naver.com/hospital/1/review/visitor"}}
    buf = io.StringIO()
    with patch("collectors.naver.polite_sleep"), \
         patch("collectors.naver._via_graphql", return_value=(None, "graphql status=429 (차단/캡차로 추정)")), \
         patch("collectors.naver._via_embedded_state", return_value=([{"content": "x"}], None)), \
         contextlib.redirect_stdout(buf):
        reviews = collect_full(hospital)
    out = buf.getvalue()
    assert reviews == [{"content": "x"}]
    assert "::warning" in out and "test_clinic" in out and "status=429" in out, f"실제 출력: {out!r}"
    print("PASS: test_collect_full_warns_with_graphql_error_when_falling_back")


if __name__ == "__main__":
    test_collect_full_warns_with_graphql_error_when_falling_back()
    test_collect_full_rejects_booking_and_short_urls()
    test_extract_place_id_hospital_segment()
    test_extract_place_id_no_digits()
    test_via_graphql_success()
    test_via_graphql_blocked()
    test_collect_full_raises_when_place_id_missing()
    test_collect_full_success_via_graphql()


def test_via_embedded_state_decodes_utf8_without_charset_header():
    # 2026-10-12 실측: 네이버 리뷰 페이지는 Content-Type에 charset 없이 "text/html"만 보내,
    # requests가 ISO-8859-1로 읽어 본문이 깨진 채 시트에 들어갔다(더안청담의원 10건).
    import requests
    html = ('<script>window.__APOLLO_STATE__ = {"VisitorReview:1": '
            '{"body": "친절하시고 시술도 꼼꼼히", "rating": 5, "created": "10.1.수"}};</script>')
    resp = requests.Response()
    resp.status_code = 200
    resp.headers["Content-Type"] = "text/html"
    resp._content = html.encode("utf-8")
    resp.encoding = requests.utils.get_encoding_from_headers(resp.headers)  # 실제 어댑터와 동일: ISO-8859-1
    with patch("collectors.naver.requests.get", return_value=resp):
        reviews, err = _via_embedded_state("https://m.place.naver.com/hospital/1/review/visitor")
    assert err is None
    assert reviews[0]["content"] == "친절하시고 시술도 꼼꼼히"
