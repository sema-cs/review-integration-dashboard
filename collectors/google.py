"""구글맵 리뷰 수집기 (헤드리스 브라우저 렌더링).

"리뷰 통합 관리 html/backend/collectors/google.py"(2026-09-11 실측 검증됨)에서
이식. light_ping()은 이 프로젝트에 실시간 알림이 없어 제거했다.

DOM 셀렉터(2026-09-11 실측, 강남제이스타의원 기준. 구글 지도 클래스명은
해시형이라 자주 바뀔 수 있음 — 값이 전부 0건으로 나오면 셀렉터를 다시
확보해야 한다):
    .jftiEf         리뷰 카드
    .d4r55          작성자
    .rsqaWe         작성일(상대 표기)
    span[role=img]  aria-label="별표 N개"에서 별점 추출
    .wiI7pd         리뷰 본문 (사장님 답글도 같은 클래스 — 구분 필요)
    .CDe7pd         사장님 답글 컨테이너
"""

import re
from datetime import datetime, timedelta

from playwright.sync_api import sync_playwright

from collectors.base import polite_sleep

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

MAX_SCROLL_ROUNDS = 20

_DAYS_IN_MONTH = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]


def _is_leap(year):
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _subtract_months(dt, months):
    total = dt.year * 12 + (dt.month - 1) - months
    year, month = divmod(total, 12)
    month += 1
    max_day = _DAYS_IN_MONTH[month - 1] + (1 if month == 2 and _is_leap(year) else 0)
    return dt.replace(year=year, month=month, day=min(dt.day, max_day))


def parse_relative_date(text, now=None):
    """"10개월 전", "2주 전", "어제" 같은 구글의 상대 날짜 표기를 절대 날짜로
    변환한다. 변환 실패 시 None."""
    now = now or datetime.now()
    text = (text or "").strip()

    m = re.search(r"(\d+)\s*년\s*전", text)
    if m:
        return _subtract_months(now, int(m.group(1)) * 12).strftime("%Y-%m") + f" ({text})"
    m = re.search(r"(\d+)\s*개월\s*전", text)
    if m:
        return _subtract_months(now, int(m.group(1))).strftime("%Y-%m") + f" ({text})"
    m = re.search(r"(\d+)\s*주\s*전", text)
    if m:
        return (now - timedelta(weeks=int(m.group(1)))).strftime("%Y-%m-%d") + f" ({text})"
    m = re.search(r"(\d+)\s*일\s*전", text)
    if m:
        return (now - timedelta(days=int(m.group(1)))).strftime("%Y-%m-%d") + f" ({text})"
    if "어제" in text:
        return (now - timedelta(days=1)).strftime("%Y-%m-%d") + f" ({text})"
    if "오늘" in text:
        return now.strftime("%Y-%m-%d") + f" ({text})"
    return None


_EXTRACT_VISIBLE_JS = """() => {
    return Array.from(document.querySelectorAll('.jftiEf')).map(card => {
        const authorEl = card.querySelector('.d4r55');
        const dateEl = card.querySelector('.rsqaWe');
        const starEl = card.querySelector("span[role='img']");
        const replyBlock = card.querySelector('.CDe7pd');
        let content = '';
        for (const el of card.querySelectorAll('.wiI7pd')) {
            if (replyBlock && replyBlock.contains(el)) continue;
            content = el.textContent.trim();
            break;
        }
        return {
            author: authorEl ? authorEl.textContent.trim() : '익명',
            ratingLabel: starEl ? starEl.getAttribute('aria-label') : null,
            rawDate: dateEl ? dateEl.textContent.trim() : null,
            content: content,
            hasReply: !!replyBlock,
        };
    });
}"""

_EXPAND_TRUNCATED_JS = """() => {
    const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    const targets = [];
    let n;
    while (n = w.nextNode()) {
        if (n.textContent.trim() === '자세히 보기' && n.parentElement) targets.push(n.parentElement);
    }
    targets.forEach(el => el.click());
    return targets.length;
}"""

_SCROLL_STEP_JS = """(fraction) => {
    const card = document.querySelector('.jftiEf');
    if (!card) return null;
    let el = card;
    while (el && !(el.scrollHeight > el.clientHeight + 10)) el = el.parentElement;
    if (!el) return null;
    el.scrollTop += el.clientHeight * fraction;
    return { scrollTop: el.scrollTop, scrollHeight: el.scrollHeight, clientHeight: el.clientHeight };
}"""

# 리뷰 카드가 아직 없을 때 쓰는 스크롤: 카드 대신 장소 패널의 스크롤 영역을 찾는다.
_SCROLL_PANEL_JS = """(fraction) => {
    const els = [...document.querySelectorAll('[role=main] *')].filter(e =>
        e.scrollHeight > e.clientHeight + 10 && /auto|scroll/.test(getComputedStyle(e).overflowY));
    if (!els.length) return null;
    els[0].scrollTop += els[0].clientHeight * fraction;
    return els[0].scrollTop;
}"""


def _to_review_dict(raw):
    rating = None
    if raw.get("ratingLabel"):
        m = re.search(r"(\d+)", raw["ratingLabel"])
        if m:
            rating = int(m.group(1))
    raw_date = raw.get("rawDate")
    date = parse_relative_date(raw_date) or raw_date
    return {
        "channel": "구글",
        "author": raw.get("author") or "익명",
        "rating": rating,
        "date": date,
        "content": raw.get("content") or "",
        "has_reply": bool(raw.get("hasReply")),
    }


def _scrape_reviews(place_url):
    """구글 지도 리뷰 패널은 진짜 가상 스크롤이라, 스크롤하는 매 순간마다
    보이는 카드를 읽어 누적해야 전체를 놓치지 않는다(2026-09-11 실측)."""
    collected = {}

    def _collect_visible(page):
        for raw in page.evaluate(_EXTRACT_VISIBLE_JS):
            key = (raw.get("author"), raw.get("rawDate"), (raw.get("content") or "")[:40])
            collected[key] = raw

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=USER_AGENT, locale="ko-KR")
            page.goto(place_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)

            review_tab = page.get_by_role("tab", name=re.compile("리뷰"))
            try:
                review_tab.first.click(timeout=20000)
            except Exception:
                page.reload(wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(2000)
                review_tab = page.get_by_role("tab", name=re.compile("리뷰"))
                review_tab.first.click(timeout=20000)
            # "온라인 예약" 블록이 있는 병원은 리뷰 카드가 첫 화면 아래로 밀려
            # 패널을 스크롤하기 전에는 카드가 아예 생성되지 않는다(2026-10-16 실측,
            # 넬의원·더바디성형외과 0건의 원인). 카드가 없으면 패널을 먼저 내린다.
            for _ in range(5):
                if page.locator(".jftiEf").count():
                    break
                page.evaluate(_SCROLL_PANEL_JS, 0.8)
                page.wait_for_timeout(1500)
            page.wait_for_selector(".jftiEf", timeout=15000)

            stable_rounds = 0
            prev_scroll_top = -1
            for _ in range(MAX_SCROLL_ROUNDS):
                page.evaluate(_EXPAND_TRUNCATED_JS)
                page.wait_for_timeout(200)
                _collect_visible(page)

                pos = page.evaluate(_SCROLL_STEP_JS, 0.8)
                if not pos:
                    break
                if pos["scrollTop"] == prev_scroll_top:
                    stable_rounds += 1
                    if stable_rounds >= 2:
                        break
                else:
                    stable_rounds = 0
                prev_scroll_top = pos["scrollTop"]
                page.wait_for_timeout(1800)

            page.evaluate(_EXPAND_TRUNCATED_JS)
            page.wait_for_timeout(300)
            _collect_visible(page)

            return list(collected.values())
        finally:
            browser.close()


def collect_full(hospital):
    """전체 리뷰 수집. google_place_id는 `/maps/place/{상호명}/data=!4m2!3m1!1s{id}`
    형태 URL이어야 동작한다(`?q=place_id:` 쿼리는 실패 — 2026-09-11 실측)."""
    place_id = hospital["channels"]["google_place_id"]
    slug = hospital["hospital_name"]
    place_url = f"https://www.google.com/maps/place/{slug}/data=!4m2!3m1!1s{place_id}?hl=ko"
    polite_sleep()
    raw_reviews = _scrape_reviews(place_url)
    return [_to_review_dict(r) for r in raw_reviews]
