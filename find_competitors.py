"""자사 병원별 인근 경쟁 병원 후보를 찾고(카카오맵 웹 검색) 상위 15곳을 고른다.

강남제이스타의원(2026-09-22) 방식을 28곳에 확장한 온보딩 도구다(2026-10-13 사용자 확정).
- 행정구역: 도 소속은 시, 특별·광역시는 구. 검색어는 "시도 시/구 진료과"(구 이름 중복 방지).
- 1차 필터(상위 노출): 카카오맵 검색 결과 1~3페이지(최대 45곳). 카카오맵 화면이 스스로
  불러오는 검색 응답을 읽을 뿐 별도 요청·우회는 하지 않는다. 차단 징후가 보이면 즉시 중단한다.
- 선정: 같은 시/구 주소 + 같은 진료과 분류만 남기고, 15곳 이하면 전부, 넘으면
  (노출 순위 백분위 + 거리 백분위)/2 점수가 낮은 순 15곳. 동점은 카카오 장소 ID 순.

사용:
  python find_competitors.py search            # 검색 원본 → competitor_onboarding_work/search_raw.json
  python find_competitors.py select            # 선정 → competitor_onboarding_work/selected.json
  python find_competitors.py channels          # 네이버·구글 채널 대조 → competitor_onboarding_work/channels.json
"""

import json
import re
import math
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORK = ROOT / "competitor_onboarding_work"
HOSPITALS_CONFIG = ROOT / "config" / "hospitals_config.json"

MAX_PER_HOSPITAL = 15
SEARCH_PAGES = 3
# 강남제이스타는 2026-09-22 실측 목록(22곳)을 그대로 유지한다
SKIP_HOSPITALS = {"gangnam_jstar"}

METRO_PREFIXES = ("서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종")

# 자사 진료과 → 카카오 검색어/분류(cate_name_depth3) 목록
CATEGORY_QUERIES = {
    "피부과·성형외과": ["피부과", "성형외과"],
    # 다니엘웰니스의원: 카카오 분류는 "일반의원"이지만 성형외과·피부과·내과 진료(2026-10-15 사용자 확인)
    "성형외과·피부과·내과": ["성형외과", "피부과", "내과"],
}

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")


def region_of(address):
    """주소 → (시도, 시/구). 도 소속은 시, 특별·광역시는 구."""
    t = address.split()
    return t[0], t[1]


def queries_for(hospital):
    sido, sigu = region_of(hospital["address"])
    cats = CATEGORY_QUERIES.get(hospital["category"], [hospital["category"]])
    return [(cat, f"{sido} {sigu} {cat}") for cat in cats]


def haversine_km(lat1, lng1, lat2, lng2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _kakao_id(url):
    return (url or "").rstrip("/").split("/")[-1]


def select_competitors(hospital, pools, own_kakao_ids, own_names, limit=MAX_PER_HOSPITAL):
    """pools: {진료과: [검색 결과 place dict(노출 순서대로)]} → 선정 목록(점수순).

    순수 함수(테스트 대상). 같은 입력이면 항상 같은 결과."""
    sido, sigu = region_of(hospital["address"])
    merged = {}
    for cat, places in pools.items():
        for rank, pl in enumerate(places):
            addr_t = (pl.get("new_address") or pl.get("address") or "").split()
            if len(addr_t) < 2 or addr_t[0] != sido or addr_t[1] != sigu:
                continue
            if pl.get("cate_name_depth3") != cat:
                continue
            pid = str(pl["confirmid"])
            if pid in own_kakao_ids or pl.get("name") in own_names:
                continue
            prev = merged.get(pid)
            if prev is None or rank < prev["rank"]:
                merged[pid] = {"place": pl, "rank": rank, "category": cat}
    cands = list(merged.values())
    for c in cands:
        c["distance_km"] = round(haversine_km(hospital["lat"], hospital["lng"],
                                              float(c["place"]["lat"]), float(c["place"]["lon"])), 3)
    n = len(cands)
    if n == 0:
        return []
    by_rank = sorted(cands, key=lambda c: (c["rank"], c["place"]["confirmid"]))
    by_dist = sorted(cands, key=lambda c: (c["distance_km"], c["place"]["confirmid"]))
    for i, c in enumerate(by_rank):
        c["rank_pct"] = i / n
    for i, c in enumerate(by_dist):
        c["dist_pct"] = i / n
    for c in cands:
        c["score"] = round((c["rank_pct"] + c["dist_pct"]) / 2, 4)
    cands.sort(key=lambda c: (c["score"], str(c["place"]["confirmid"])))
    return cands[:limit]


def search(only=None):
    from playwright.sync_api import sync_playwright

    WORK.mkdir(exist_ok=True)
    out_path = WORK / "search_raw.json"
    raw = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else {}
    hospitals = json.loads(HOSPITALS_CONFIG.read_text(encoding="utf-8"))["hospitals"]
    todo = []
    for h in hospitals:
        if h["hospital_id"] in SKIP_HOSPITALS or (only and h["hospital_id"] not in only):
            continue
        for _cat, q in queries_for(h):
            if q not in raw:
                todo.append(q)
    todo = sorted(set(todo))
    print(f"검색할 질의 {len(todo)}건 (이미 받은 {len(raw)}건은 건너뜀)")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=UA, locale="ko-KR")
            got = {}

            def on_resp(r):
                if "mapsearch/map" in r.url and r.status == 200:
                    try:
                        d = r.json()
                        got[d.get("page") or 1] = d
                    except Exception:
                        pass
                elif "mapsearch/map" in r.url:
                    got["error"] = r.status

            page.on("response", on_resp)
            for q in todo:
                got.clear()
                page.goto("https://map.kakao.com/?q=" + q, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(4000)
                for n in range(2, SEARCH_PAGES + 1):
                    if "error" in got:
                        break
                    # "장소 더보기"가 2페이지를 불러오는데, 응답이 늦으면 놓친다(2026-10-13 실측:
                    # 용인시·서초구 치과 1·3페이지만 수집) — 페이지 번호 링크로 한 번 더 확인한다.
                    for _attempt in range(2):
                        if n in got:
                            break
                        more = page.locator("[id='info.search.place.more']")
                        link = page.locator(f"[id='info.search.page.no{n}']")
                        if link.count() and link.is_visible():
                            link.click()
                        elif more.count() and more.is_visible():
                            more.click()
                        else:
                            break
                        page.wait_for_timeout(3500 + random.random() * 1500)
                if "error" in got:
                    print(f"중단: '{q}' 검색 응답 status={got['error']} (차단 가능성 — 우회하지 않음)")
                    break
                pages = sorted(k for k in got if isinstance(k, int))
                places = []
                seen = set()
                for k in pages:
                    for pl in got[k].get("place") or []:
                        if pl["confirmid"] not in seen:
                            seen.add(pl["confirmid"])
                            places.append(pl)
                total = got[pages[0]].get("place_totalcount") if pages else None
                raw[q] = {"pages": pages, "total": total, "places": places}
                print(f"{q}: 전체 {total}곳 중 상위 {len(places)}곳 수집 (페이지 {pages})")
                out_path.write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
                time.sleep(3 + random.random() * 3)
        finally:
            browser.close()


def select():
    raw = json.loads((WORK / "search_raw.json").read_text(encoding="utf-8"))
    hospitals = json.loads(HOSPITALS_CONFIG.read_text(encoding="utf-8"))["hospitals"]
    own_ids = {_kakao_id(h["channels"].get("kakao_place_url")) for h in hospitals}
    own_names = {h["hospital_name"] for h in hospitals}
    result = {}
    for h in hospitals:
        if h["hospital_id"] in SKIP_HOSPITALS:
            continue
        pools = {}
        for cat, q in queries_for(h):
            if q not in raw:
                print(f"검색 원본 없음: {q}")
                continue
            pools[cat] = raw[q]["places"]
        chosen = select_competitors(h, pools, own_ids, own_names)
        result[h["hospital_id"]] = [{
            "kakao_id": str(c["place"]["confirmid"]),
            "name": c["place"]["name"],
            "category": c["category"],
            "address": c["place"].get("new_address") or c["place"].get("address"),
            "jibun_address": c["place"].get("address"),
            "tel": c["place"].get("tel"),
            "lat": float(c["place"]["lat"]),
            "lng": float(c["place"]["lon"]),
            "search_rank": c["rank"] + 1,
            "distance_km": c["distance_km"],
            "score": c["score"],
        } for c in chosen]
        print(f"{h['hospital_id']}: {len(chosen)}곳 선정")
    (WORK / "selected.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")


_ROAD_RE = re.compile(r"([가-힣A-Za-z0-9]+(?:로|길))\s*(\d+(?:-\d+)?)")
_JIBUN_RE = re.compile(r"([가-힣0-9]+(?:동|가|리|읍|면))\s*(?:산\s*)?(\d+(?:-\d+)?)")


def road_key(address):
    """'서울 강남구 강남대로 248 목원빌딩 3층' → ('강남대로', '248'). 없으면 None."""
    m = _ROAD_RE.search(address or "")
    return (m.group(1), m.group(2)) if m else None


def jibun_key(address):
    # 구글은 지번을 "부평동 194 23번지"로 적는다(2026-10-13 실측) → "194-23"으로 맞춘다
    s = re.sub(r"(\d+)\s+(\d+)\s*번지", r"\1-\2", address or "").replace("번지", "")
    m = _JIBUN_RE.search(s)
    return (m.group(1), m.group(2)) if m else None


def same_address(kakao, other_text):
    """카카오 장소와 다른 채널 주소가 같은 건물인지(도로명+건물번호 또는 지번 일치)."""
    kr, orr = road_key(kakao.get("address")), road_key(other_text)
    if kr and orr and kr == orr:
        return True
    kj, oj = jibun_key(kakao.get("jibun_address")), jibun_key(other_text)
    return bool(kj and oj and kj == oj)


_NAME_SUFFIXES = ("한방병원", "한의원", "이비인후과", "정형외과", "성형외과", "산부인과", "여성의원",
                  "피부과", "외과", "치과", "병원", "의원")


def name_core(name):
    s = re.sub(r"[^가-힣A-Za-z0-9]", "", name or "")
    for suf in _NAME_SUFFIXES:
        s = s.replace(suf, "")
    return s


def same_name(a, b):
    """'서울이자라는치과의원' ~ '서울이자라는치과', '블리비의원 인천부평점' ~ '블리비의원 인천 부평점'."""
    ca, cb = name_core(a), name_core(b)
    if not ca or not cb:
        return False
    return ca in cb or cb in ca


_APOLLO_RE = re.compile(r"__APOLLO_STATE__\s*=\s*(\{.*?\});\s*\n", re.S)


def naver_candidates(html):
    """네이버 검색 결과의 장소 후보. 결과가 여럿이면 PlaceListBusinessesItem, 1곳이면
    PlaceDetailBase로 나온다(2026-10-13 실측). [{id, name, address_text}]"""
    m = _APOLLO_RE.search(html)
    if not m:
        return None
    try:
        state = json.loads(m.group(1))
    except ValueError:
        return None
    out = []
    for k, v in state.items():
        if not isinstance(v, dict):
            continue
        if k.startswith("PlaceListBusinessesItem:"):
            out.append({"id": v["id"], "name": v.get("normalizedName") or v.get("name", ""),
                        "address_text": " ".join(filter(None, [v.get("fullAddress"), v.get("roadAddress"),
                                                               v.get("commonAddress"), v.get("address")]))})
        elif k.startswith("PlaceDetailBase:"):
            out.append({"id": v["id"], "name": v.get("name", ""),
                        "address_text": " ".join(filter(None, [v.get("roadAddress"), v.get("address")]))})
    return out


def pick_naver(kakao, cands):
    for v in cands:
        if same_address(kakao, v["address_text"]) and same_name(kakao["name"], v["name"]):
            return v
    return None


def accept_google(kakao, g_name, g_addr):
    """구글은 영문 상호로만 등록된 곳이 있어(예: 신사에그의원→EGGCLINIC) 이름에 한글이 없으면
    주소 일치만으로 받는다. 한글 이름이면 주소+이름 둘 다 맞아야 한다."""
    if not same_address(kakao, g_addr):
        return False
    if not re.search(r"[가-힣]", g_name or ""):
        return True
    return same_name(kakao["name"], g_name)


def _google_lookup(page, query):
    from urllib.parse import quote
    page.goto("https://www.google.com/maps/search/" + quote(query) + "?hl=ko",
              wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(4500)
    if "/sorry/" in page.url:
        return "blocked", None, None
    ftid = re.findall(r"!1s(0x[0-9a-f]+:0x[0-9a-f]+)", page.url)
    if not ftid:
        return None, None, None
    g_addr = " ".join(page.locator("button[data-item-id='address']").all_inner_texts())
    h1 = page.locator("h1").all_inner_texts()
    return ftid[0], (h1[0] if h1 else ""), g_addr


def lookup_channels():
    """selected.json의 경쟁 병원마다 네이버 플레이스 ID·구글 장소 ID를 찾아 channels.json에 누적."""
    from urllib.parse import quote
    from playwright.sync_api import sync_playwright

    selected = json.loads((WORK / "selected.json").read_text(encoding="utf-8"))
    out_path = WORK / "channels.json"
    done = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else {}
    uniq = {}
    for comps in selected.values():
        for c in comps:
            uniq.setdefault(c["kakao_id"], c)
    todo = [c for k, c in sorted(uniq.items()) if k not in done]
    print(f"채널 조회 대상 {len(todo)}곳 (완료 {len(done)}곳)")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=UA, locale="ko-KR")
            for i, c in enumerate(todo, 1):
                sido, sigu = region_of(c["address"])
                rec = {"naver_place_id": "", "naver_name": "", "google_place_id": "", "google_name": ""}

                page.goto("https://m.search.naver.com/search.naver?query=" + quote(f"{c['name']} {sigu}"),
                          wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(2500)
                cands = naver_candidates(page.content())
                if cands is None:
                    if "captcha" in page.url or page.locator("#captcha, .captcha_wrap").count():
                        print(f"중단: 네이버 캡차 ({c['name']}) — 우회하지 않음. 다시 실행하면 이어서 진행")
                        break
                    cands = []
                hit = pick_naver(c, cands)
                if hit:
                    rec["naver_place_id"], rec["naver_name"] = hit["id"], hit["name"]
                time.sleep(2 + random.random() * 2)

                blocked = False
                for q in (f"{c['name']} {c['address']}", f"{c['name']} {sido} {sigu}"):
                    ftid, g_name, g_addr = _google_lookup(page, q)
                    if ftid == "blocked":
                        blocked = True
                        break
                    if ftid and accept_google(c, g_name, g_addr):
                        rec["google_place_id"], rec["google_name"] = ftid, g_name
                        break
                    time.sleep(2 + random.random() * 2)
                if blocked:
                    print(f"중단: 구글 비정상 트래픽 화면 ({c['name']}) — 우회하지 않음")
                    break
                done[c["kakao_id"]] = rec
                out_path.write_text(json.dumps(done, ensure_ascii=False, indent=1), encoding="utf-8")
                print(f"[{i}/{len(todo)}] {c['name']}: 네이버 {rec['naver_place_id'] or '-'} / 구글 {rec['google_place_id'] or '-'}")
                time.sleep(3 + random.random() * 3)
        finally:
            browser.close()


COMPETITORS_CONFIG = ROOT / "config" / "competitors_config.json"

CONFIG_COMMENT = (
    "자사 병원별 인근 경쟁 병원. 각 경쟁 병원은 for_hospitals에 적힌 자사 병원 화면에 보이고, 같은 곳은 "
    "한 번만 수집한다. gangnam_jstar의 22곳(comp_audrey 등)은 2026-09-22 원주시 전체를 카카오맵/구글맵으로 "
    "전수 확인한 실측 목록이다. 나머지(comp_k<카카오ID>)는 2026-10-13 find_competitors.py로 선정했다: "
    "행정구역(도 소속=시, 특별·광역시=구) + 같은 진료과로 카카오맵 검색 상위 45곳 → 같은 시/구·진료과만 → "
    "15곳 이하면 전부, 넘으면 (노출순위 백분위+거리 백분위)/2 상위 15곳. 네이버·구글은 주소+이름이 모두 "
    "일치할 때만 연결했고, 빈 값은 그 채널을 못 찾은 것(그 채널만 수집에서 건너뜀). 수집은 main.py가 "
    "하루 90분 예산 안에서 순번으로 돈다."
)


def build_config(old_config, selected, channels, own_naver_ids=(), own_google_ids=()):
    """순수 함수: 기존 설정 + 선정 결과 + 채널 대조 결과 → 새 competitors_config dict.

    자사 병원과 같은 네이버/구글 장소로 연결된 채널은 비운다(2026-10-15 실측: 같은 건물
    다니엘성형외과의원이 이름 부분일치로 자사 다니엘웰니스의원 장소에 연결됨)."""
    kept = []
    for c in old_config.get("competitors", []):
        # 이전에 이 도구로 만든 항목(comp_k<숫자>)만 다시 만든다. 단순 접두사 비교는 기존 실측
        # 항목 comp_kangyc까지 지웠다(2026-10-13 실측) — 반드시 숫자까지 확인한다.
        if re.fullmatch(r"comp_k\d+", c["competitor_id"]):
            continue
        kept.append(dict(c, for_hospitals=c.get("for_hospitals") or [old_config.get("self_hospital_id", "gangnam_jstar")]))
    new = {}
    for hid in sorted(selected):
        for s in selected[hid]:
            cid = f"comp_k{s['kakao_id']}"
            ch = dict(channels[s["kakao_id"]])
            if ch["naver_place_id"] in own_naver_ids:
                ch["naver_place_id"] = ""
            if ch["google_place_id"] in own_google_ids:
                ch["google_place_id"] = ""
            entry = new.setdefault(cid, {
                "competitor_id": cid,
                "name": s["name"],
                "category": s["category"],
                "lat": s["lat"],
                "lng": s["lng"],
                "address": s["address"],
                "for_hospitals": [],
                "selection": {},
                "channels": {
                    "kakao_place_url": f"https://place.map.kakao.com/{s['kakao_id']}",
                    "google_place_id": ch["google_place_id"],
                    "naver_place_url": (f"https://m.place.naver.com/hospital/{ch['naver_place_id']}/review/visitor"
                                        if ch["naver_place_id"] else ""),
                },
            })
            entry["for_hospitals"].append(hid)
            entry["selection"][hid] = {"search_rank": s["search_rank"], "distance_km": s["distance_km"],
                                       "score": s["score"]}
    return {"_comment": CONFIG_COMMENT, "competitors": kept + [new[k] for k in sorted(new)]}


def apply_config():
    selected = json.loads((WORK / "selected.json").read_text(encoding="utf-8"))
    channels = json.loads((WORK / "channels.json").read_text(encoding="utf-8"))
    missing = sorted({s["kakao_id"] for v in selected.values() for s in v} - set(channels))
    if missing:
        raise SystemExit(f"채널 대조가 끝나지 않은 경쟁병원 {len(missing)}곳 — `channels`를 먼저 끝까지 실행하세요.")
    old = json.loads(COMPETITORS_CONFIG.read_text(encoding="utf-8"))
    stamp = time.strftime("%Y%m%d_%H%M%S")
    COMPETITORS_CONFIG.with_name(f"competitors_config.backup_{stamp}.json").write_text(
        json.dumps(old, ensure_ascii=False, indent=2), encoding="utf-8")
    own = [h["channels"] for h in json.loads(HOSPITALS_CONFIG.read_text(encoding="utf-8"))["hospitals"]]
    own_naver = {m.group(1) for c in own
                 for m in [re.search(r"/(\d+)/", c.get("naver_place_url") or "")] if m}
    own_google = {c["google_place_id"] for c in own if c.get("google_place_id")}
    new = build_config(old, selected, channels, own_naver, own_google)
    COMPETITORS_CONFIG.write_text(json.dumps(new, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    comps = new["competitors"]
    print(f"경쟁병원 {len(comps)}곳 기록 (네이버 {sum(1 for c in comps if c['channels']['naver_place_url'])}, "
          f"구글 {sum(1 for c in comps if c['channels']['google_place_id'])}, 카카오 {len(comps)})")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "apply":
        apply_config()
        sys.exit(0)
    if cmd == "search":
        search(set(sys.argv[2:]) or None)
    elif cmd == "select":
        select()
    elif cmd == "channels":
        lookup_channels()
    else:
        print(__doc__)
