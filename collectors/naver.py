"""네이버 스마트플레이스 리뷰 수집기.

"리뷰 통합 관리 html/backend/collectors/naver.py"(2026-09-09/09-11 실측
검증됨)에서 이식. light_ping()은 이 프로젝트에 실시간 알림이 없어(설계문서
확정사항) 제거했다. 나머지 로직(GraphQL 우선 → Apollo state → HTML 셀렉터
3단 폴백, 차단 시 명시적 NaverBlockedError)은 그대로 유지한다.
"""

import json
import re

import requests
from bs4 import BeautifulSoup

from collectors.base import polite_sleep

MOBILE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
    "Referer": "https://m.place.naver.com/",
    "Accept-Language": "ko-KR,ko;q=0.9",
}

GRAPHQL_HEADERS = dict(MOBILE_HEADERS, **{"Content-Type": "application/json", "Accept": "*/*", "Origin": "https://m.place.naver.com"})

GRAPHQL_URL = "https://pcmap-api.place.naver.com/graphql"

_VISITOR_REVIEWS_QUERY = """
query getVisitorReviews($input: VisitorReviewsInput) {
  visitorReviews(input: $input) {
    items {
      id
      rating
      body
      created
      visitCount
      author { nickname }
      reply { body }
    }
    total
  }
}
"""


class NaverBlockedError(Exception):
    """네이버가 자동화 요청으로 감지해 차단했을 때, 또는 businessId를 추출하지
    못했을 때 발생한다. 이 예외를 우회하지 않는다 — 수동 입력으로 대체한다."""


def _extract_place_id(naver_place_url):
    """URL 경로에서 숫자로만 된 첫 세그먼트를 businessId로 뽑는다.
    (업종별 세그먼트명이 "hospital"/"beauty" 등으로 달라도 안전하게 동작)"""
    parts = [p for p in naver_place_url.split("/") if p]
    for p in parts:
        if p.isdigit():
            return p
    return None


_BLOCK_MARKERS = ("captcha", "자동화된 요청", "비정상적인 접근", "unusual traffic", "차단")


def _looks_blocked(resp):
    if resp.status_code in (401, 403, 429):
        return True
    body_head = resp.text[:3000].lower()
    return any(marker.lower() in body_head for marker in _BLOCK_MARKERS)


def _via_graphql(place_id, page=1, display=30):
    headers = dict(GRAPHQL_HEADERS, Referer=f"https://m.place.naver.com/hospital/{place_id}/review/visitor")
    body = [{
        "operationName": "getVisitorReviews",
        "variables": {"input": {"businessId": str(place_id), "businessType": "hospital", "page": page, "display": display}},
        "query": _VISITOR_REVIEWS_QUERY,
    }]
    resp = requests.post(GRAPHQL_URL, headers=headers, json=body, timeout=15)
    if _looks_blocked(resp):
        return None, f"graphql status={resp.status_code} (차단/캡차로 추정)"
    try:
        data = resp.json()
    except ValueError:
        return None, "graphql 응답이 JSON이 아님 (쿼리 스키마가 실제와 다를 수 있음)"

    try:
        items = data[0]["data"]["visitorReviews"]["items"]
    except (KeyError, IndexError, TypeError):
        return None, f"graphql 응답 구조가 예상과 다름: {json.dumps(data, ensure_ascii=False)[:300]}"

    reviews = [
        {
            "channel": "네이버",
            "author": (item.get("author") or {}).get("nickname", "익명"),
            "rating": item.get("rating"),
            "date": item.get("created"),
            "content": item.get("body", ""),
            "has_reply": bool(item.get("reply") and item["reply"].get("body")),
        }
        for item in items
    ]
    return reviews, None


_APOLLO_STATE_RE = re.compile(r"window\.__APOLLO_STATE__\s*=\s*(\{.*?\})\s*;?\s*</script>", re.S)


def _get_page(review_page_url):
    resp = requests.get(review_page_url, headers=MOBILE_HEADERS, timeout=15)
    # 2026-10-12 실측: Content-Type이 charset 없이 "text/html"이라 requests가 ISO-8859-1로
    # 읽어 한글 본문이 깨진 채 시트에 들어갔다(더안청담의원 10건). 페이지는 실제로 UTF-8이다.
    resp.encoding = "utf-8"
    return resp


def _via_embedded_state(review_page_url):
    resp = _get_page(review_page_url)
    if _looks_blocked(resp):
        return None, f"페이지 status={resp.status_code} (차단/캡차로 추정)"

    m = _APOLLO_STATE_RE.search(resp.text)
    if not m:
        return None, "__APOLLO_STATE__ 를 찾지 못함"
    try:
        state = json.loads(m.group(1))
    except ValueError:
        return None, "__APOLLO_STATE__ JSON 파싱 실패"

    review_entries = [v for k, v in state.items() if isinstance(v, dict) and "VisitorReview" in k]
    if not review_entries:
        return None, "__APOLLO_STATE__ 안에 리뷰 데이터 없음"

    reviews = [
        {
            "channel": "네이버",
            "author": ((entry.get("author") or {}).get("nickname", "익명")),
            "rating": entry.get("rating"),
            "date": entry.get("created"),
            "content": entry.get("body", ""),
        }
        for entry in review_entries
    ]
    return reviews, None


SELECTORS = {
    "review_card": "li.place_apply_pui",
    "author": ".pui__NMi-Dp",
    "date": ".pui__gfuUIT time",
    "content": ".pui__vn15t2",
}


def _via_html_selectors(review_page_url):
    resp = _get_page(review_page_url)
    if _looks_blocked(resp):
        return None, f"페이지 status={resp.status_code} (차단/캡차로 추정)"
    soup = BeautifulSoup(resp.text, "html.parser")
    cards = soup.select(SELECTORS["review_card"])
    if not cards:
        return None, "CSS 셀렉터로 리뷰 카드를 찾지 못함 (구조 변경 또는 SPA 렌더링)"
    reviews = []
    for c in cards:
        author_el = c.select_one(SELECTORS["author"])
        date_el = c.select_one(SELECTORS["date"])
        content_el = c.select_one(SELECTORS["content"])
        reviews.append({
            "channel": "네이버",
            "author": author_el.get_text(strip=True) if author_el else "익명",
            "rating": None,
            "date": date_el.get_text(strip=True) if date_el else None,
            "content": content_el.get_text(strip=True) if content_el else "",
        })
    return reviews, None


class NaverUrlFormatError(Exception):
    """naver_place_url이 플레이스 URL이 아닐 때(예약·단축 URL) 발생한다."""


# 예약(bizes) 번호나 단축 URL은 플레이스 businessId가 아니다. 그대로 두면 GraphQL이
# 오류 없이 0건을 돌려줘 "수집 성공 0건"으로 방치된다(2026-09-28 오드리·리블룸 실측).
_NON_PLACE_URL_MARKERS = ("booking.naver.com", "naver.me/")


def _check_place_url(hospital):
    url = hospital["channels"]["naver_place_url"]
    if any(m in url for m in _NON_PLACE_URL_MARKERS):
        msg = (f"{hospital.get('hospital_id')}: naver_place_url이 플레이스 URL이 아닙니다({url}). "
               "m.place.naver.com/hospital/<플레이스ID>/review/visitor 형식으로 바꿔야 합니다 "
               "(find_naver_place_ids.py --make-manual 참고).")
        # GitHub Actions 실행 화면에 경고로 보이게 한다
        print(f"::warning title=네이버 URL 형식 오류::{msg}")
        raise NaverUrlFormatError(msg)


def collect_full(hospital):
    _check_place_url(hospital)
    place_id = _extract_place_id(hospital["channels"]["naver_place_url"])
    if not place_id:
        raise NaverBlockedError(
            f"naver_place_url에서 businessId(숫자)를 추출하지 못했습니다: "
            f"{hospital['channels']['naver_place_url']!r}"
        )
    review_page_url = hospital["channels"]["naver_place_url"]
    polite_sleep()

    failures = []

    reviews, err = _via_graphql(place_id)
    if reviews is not None:
        return reviews
    failures.append(f"graphql: {err}")

    reviews, err = _via_embedded_state(review_page_url)
    if reviews is not None:
        return reviews
    failures.append(f"embedded_state: {err}")

    reviews, err = _via_html_selectors(review_page_url)
    if reviews is not None:
        return reviews
    failures.append(f"html_selectors: {err}")

    raise NaverBlockedError(
        "네이버 리뷰 수집 3가지 방법이 모두 실패했습니다 — " + " / ".join(failures) +
        ". 수동 입력이 필요합니다."
    )
