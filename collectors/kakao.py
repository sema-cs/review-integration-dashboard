"""카카오맵 리뷰 수집기 (헤드리스 브라우저 렌더링).

"리뷰 통합 관리 html/backend/collectors/kakao.py"(2026-09-11 실측 검증됨)에서
이식. light_ping()은 이 프로젝트에 실시간 알림이 없어 제거했다(카카오는
원래도 브라우저 비용 때문에 light_ping에서 제외돼 있었음).

DOM 셀렉터(2026-09-11 실측, 강남제이스타의원 기준):
    .group_review .list_review > li   리뷰 카드
    .name_user                        작성자 (숨김 span "리뷰어 이름, " 포함 — 제거 필요)
    .starred_grade > span.screen_out  [0]="별점" 라벨, [1]="5.0" 실제 점수
    .review_detail .txt_date          리뷰 작성일
    .wrap_review .desc_review         리뷰 본문
    .info_reply                       있으면 사장님 답글 존재
    (2026-10-12) "#review" 탭 + 스크롤로 추가 로딩 — 더보기 버튼 없음
"""

from bs4 import BeautifulSoup
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from collectors.base import polite_sleep

# 2026-10-12 실측: 고정 문자열 "Chrome/124.0"은 카카오 게이트웨이가 403
# "Restricted User Agent"로 막았다(9-16 이후 카카오 수집 0건의 원인). 버전이
# 낡으면 또 막히므로 고정하지 않고, 실행 중인 Chromium의 실제 UA에서
# "HeadlessChrome" 표기만 일반 "Chrome"으로 바꿔 쓴다(버전은 실제 엔진 그대로).
def _browser_user_agent(browser):
    return f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{browser.version} Safari/537.36"

# 2026-10-12 실측: 리뷰는 홈이 아니라 "#review" 탭에 있고(홈은 미리보기 1~3건),
# "후기 더보기" 버튼 대신 스크롤하면 다음 묶음(약 10건)이 붙는다.
MAX_SCROLL_ROUNDS = 15

# 2026-09-22 실측: 경쟁 병원이 6곳→22곳으로 늘면서 한 실행 안에서 카카오맵을
# 22번 연속 요청했더니 전원(자사 포함) ".group_review" 대기 타임아웃으로
# 실패했다(전날 6곳 실행은 실패 0건이었음). 공용 polite_sleep()의 2~3초보다
# 훨씬 길게 둬서 업체 간 요청을 더 정중하게 분산시킨다.
KAKAO_SLEEP_RANGE = (8.0, 15.0)


def _extract_place_id(kakao_place_url):
    return kakao_place_url.rstrip("/").split("/")[-1]


def _clean_text(el, drop_selectors=(".screen_out", ".btn_more", ".btn_less")):
    if el is None:
        return ""
    for sel in drop_selectors:
        for child in el.select(sel):
            child.decompose()
    return el.get_text(strip=True)


def _render_review_html(place_url):
    """실제 브라우저 렌더링 — 유닛테스트 대상이 아니다(계획 문서 Task 4 Step 6에서 수동 확인)."""
    card_sel = ".group_review .list_review > li"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=_browser_user_agent(browser), locale="ko-KR")
            page.goto(place_url + "#review", wait_until="networkidle", timeout=30000)
            try:
                page.wait_for_selector(card_sel, timeout=15000)
            except PlaywrightTimeoutError:
                # 병원이 후기를 막아 둔 곳(후기미제공)은 리뷰 0건이 정상이다.
                # 페이지 하단 랭킹 위젯에도 다른 업체의 "후기미제공"이 뜰 수 있어,
                # 리뷰 카드가 없을 때만 이 문구를 본다.
                if page.locator("text=후기미제공").count() > 0:
                    return None
                raise

            prev = -1
            for _ in range(MAX_SCROLL_ROUNDS):
                count = page.locator(card_sel).count()
                if count == prev:
                    break
                prev = count
                page.locator(card_sel).last.scroll_into_view_if_needed()
                page.mouse.wheel(0, 4000)
                page.wait_for_timeout(1500)

            page.evaluate(
                "document.querySelectorAll('.btn_more').forEach(el => el.click())"
            )
            page.wait_for_timeout(500)

            return page.content()
        finally:
            browser.close()


def _parse_reviews(html):
    soup = BeautifulSoup(html, "html.parser")
    reviews = []
    for li in soup.select(".group_review .list_review > li"):
        author = _clean_text(li.select_one(".name_user")) or "익명"

        rating = None
        grade_vals = li.select(".starred_grade > span.screen_out")
        if len(grade_vals) >= 2:
            try:
                rating = round(float(grade_vals[1].get_text(strip=True)))
            except ValueError:
                rating = None

        review_detail = li.select_one(".review_detail")
        date_el = (review_detail or li).select_one(".txt_date")
        date = date_el.get_text(strip=True) if date_el else None

        content = _clean_text(li.select_one(".wrap_review .desc_review"))

        reply_block = li.select_one(".info_reply")
        has_reply = reply_block is not None

        reviews.append(
            {
                "channel": "카카오맵",
                "author": author,
                "rating": rating,
                "date": date,
                "content": content,
                "has_reply": has_reply,
            }
        )
    return reviews


def collect_full(hospital):
    """전체 리뷰 수집 (매일 정기 집계용)."""
    place_id = _extract_place_id(hospital["channels"]["kakao_place_url"])
    place_url = f"https://place.map.kakao.com/{place_id}"
    polite_sleep(KAKAO_SLEEP_RANGE)
    html = _render_review_html(place_url)
    if html is None:
        return []
    return _parse_reviews(html)
