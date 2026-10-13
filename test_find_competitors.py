import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from find_competitors import region_of, queries_for, select_competitors

# 가상의 병원·장소(실명 사용 금지 — 테스트 픽스처 원칙)
HOSP = {"hospital_id": "test_h", "category": "치과", "address": "서울 가상구 테스트로 1",
        "lat": 37.5, "lng": 127.0}


def _pl(pid, name, addr="서울 가상구 어딘가 1", cat="치과", lat=37.5, lng=127.0):
    return {"confirmid": pid, "name": name, "new_address": addr, "address": addr,
            "cate_name_depth3": cat, "lat": lat, "lon": lng}


def test_region_and_queries():
    assert region_of("충북 청주시 흥덕구 강서로 1") == ("충북", "청주시")
    assert region_of("대전 서구 월평로 1") == ("대전", "서구")
    h = dict(HOSP, category="피부과·성형외과")
    assert queries_for(h) == [("피부과", "서울 가상구 피부과"), ("성형외과", "서울 가상구 성형외과")]
    print("PASS: test_region_and_queries")


def test_filters_region_category_and_own():
    pools = {"치과": [
        _pl("1", "가"),
        _pl("2", "나", addr="서울 다른구 어딘가 1"),   # 다른 구
        _pl("3", "다", cat="동물병원"),               # 다른 진료과
        _pl("4", "자사치과"),                          # 자사(이름)
        _pl("5", "라"),                                # 자사(카카오 ID)
    ]}
    got = select_competitors(HOSP, pools, own_kakao_ids={"5"}, own_names={"자사치과"})
    assert [c["place"]["confirmid"] for c in got] == ["1"], got
    print("PASS: test_filters_region_category_and_own")


def test_takes_all_when_under_limit_and_caps_by_score():
    # 노출 순위 1위지만 멀리, 마지막 순위지만 가까움 → 점수로 상한 내 선정
    places = [_pl(str(i), f"p{i}", lat=37.5 + 0.01 * (20 - i)) for i in range(20)]
    got = select_competitors(HOSP, {"치과": places}, set(), set(), limit=15)
    assert len(got) == 15
    got_small = select_competitors(HOSP, {"치과": places[:10]}, set(), set(), limit=15)
    assert len(got_small) == 10
    # 같은 입력 → 같은 결과
    again = select_competitors(HOSP, {"치과": places}, set(), set(), limit=15)
    assert [c["place"]["confirmid"] for c in got] == [c["place"]["confirmid"] for c in again]
    print("PASS: test_takes_all_when_under_limit_and_caps_by_score")


def test_merges_two_category_pools_keeping_best_rank():
    h = dict(HOSP, category="피부과·성형외과")
    pools = {"피부과": [_pl("9", "x", cat="피부과"), _pl("1", "a", cat="피부과")],
             "성형외과": [_pl("1", "a", cat="성형외과")]}
    got = select_competitors(h, pools, set(), set())
    ids = [c["place"]["confirmid"] for c in got]
    assert sorted(ids) == ["1", "9"], ids
    a = next(c for c in got if c["place"]["confirmid"] == "1")
    assert a["rank"] == 0 and a["category"] == "성형외과"
    print("PASS: test_merges_two_category_pools_keeping_best_rank")


if __name__ == "__main__":
    test_region_and_queries()
    test_filters_region_category_and_own()
    test_takes_all_when_under_limit_and_caps_by_score()
    test_merges_two_category_pools_keeping_best_rank()


def test_address_and_name_matching():
    from find_competitors import same_address, road_key, jibun_key, same_name, pick_naver, accept_google
    assert road_key("인천 서해구 크리스탈로74번길 31 5층") == ("크리스탈로74번길", "31")
    assert jibun_key("인천광역시 부평구 부평동 194 23번지 MH타워 4층") == ("부평동", "194-23")
    k = {"name": "가나다치과의원", "address": "경기 가상시 테스트로 105", "jibun_address": "경기 가상시 가상동 216-1"}
    assert same_address(k, "경기도 가상시 테스트로 105 1층")
    assert not same_address(k, "경기도 가상시 테스트로 107")
    assert same_address(k, "가상동 216 1번지")
    assert same_name("가나다치과의원 본점", "가나다 치과")
    assert not same_name("가나다치과의원", "라마바치과의원")
    # 같은 건물의 다른 병원은 이름으로 거른다
    cands = [{"id": "1", "name": "라마바치과의원", "address_text": "경기 가상시 테스트로 105"},
             {"id": "2", "name": "가나다치과", "address_text": "경기 가상시 테스트로 105"}]
    assert pick_naver(k, cands)["id"] == "2"
    assert accept_google(k, "GANADA DENTAL", "경기도 가상시 테스트로 105")   # 영문 상호
    assert not accept_google(k, "라마바치과", "경기도 가상시 테스트로 105")
    print("PASS: test_address_and_name_matching")


if __name__ == "__main__":
    test_address_and_name_matching()


def test_build_config_keeps_existing_and_merges_shared_competitors():
    from find_competitors import build_config
    old = {"self_hospital_id": "h0", "competitors": [
        {"competitor_id": "comp_old", "name": "o", "channels": {}},
        {"competitor_id": "comp_kim", "name": "k로 시작하는 기존 항목", "channels": {}},
        {"competitor_id": "comp_k9", "name": "stale", "channels": {}, "for_hospitals": ["h1"]}]}
    s = {"kakao_id": "5", "name": "공유치과", "category": "치과", "lat": 1.0, "lng": 2.0,
         "address": "서울 가상구 테스트로 1", "search_rank": 3, "distance_km": 0.5, "score": 0.1}
    selected = {"h2": [dict(s)], "h1": [dict(s, search_rank=1)]}
    channels = {"5": {"naver_place_id": "77", "naver_name": "", "google_place_id": "", "google_name": ""}}
    out = build_config(old, selected, channels)
    ids = [c["competitor_id"] for c in out["competitors"]]
    assert ids == ["comp_old", "comp_kim", "comp_k5"], ids
    assert out["competitors"][0]["for_hospitals"] == ["h0"]
    shared = out["competitors"][2]
    assert shared["for_hospitals"] == ["h1", "h2"]
    assert shared["channels"]["naver_place_url"] == "https://m.place.naver.com/hospital/77/review/visitor"
    assert shared["channels"]["google_place_id"] == ""
    assert shared["selection"]["h1"]["search_rank"] == 1
    print("PASS: test_build_config_keeps_existing_and_merges_shared_competitors")


if __name__ == "__main__":
    test_build_config_keeps_existing_and_merges_shared_competitors()
