import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from unittest.mock import patch
import main

_FULL_CHANNELS = {
    "naver_place_url": "https://m.place.naver.com/hospital/1/review/visitor",
    "kakao_place_url": "https://place.map.kakao.com/1",
    "google_place_id": "0x1:0x1",
}


def test_collect_hospital_aggregates_all_channels():
    hospital = {"hospital_id": "gangnam_jstar", "hospital_name": "강남제이스타의원", "channels": _FULL_CHANNELS}
    with patch("main.naver.collect_full", return_value=[{"channel": "네이버", "content": "좋아요"}]), \
         patch("main.kakao.collect_full", return_value=[{"channel": "카카오맵", "content": "별로예요"}]), \
         patch("main.google.collect_full", return_value=[{"channel": "구글", "content": "그저 그래요"}]):
        raw, errors = main.collect_hospital(hospital)
    assert len(raw) == 3, f"실제: {raw}"
    assert errors == [], f"실제: {errors}"
    print("PASS: test_collect_hospital_aggregates_all_channels")


def test_collect_hospital_isolates_single_channel_failure():
    hospital = {"hospital_id": "gangnam_jstar", "hospital_name": "강남제이스타의원", "channels": _FULL_CHANNELS}

    def boom(_hospital):
        raise ValueError("차단됨")

    with patch("main.naver.collect_full", side_effect=boom), \
         patch("main.kakao.collect_full", return_value=[{"channel": "카카오맵", "content": "별로예요"}]), \
         patch("main.google.collect_full", return_value=[{"channel": "구글", "content": "그저 그래요"}]):
        raw, errors = main.collect_hospital(hospital)
    assert len(raw) == 2, f"실제: {raw}"
    assert len(errors) == 1, f"실제: {errors}"
    assert "차단됨" in errors[0]
    print("PASS: test_collect_hospital_isolates_single_channel_failure")


def test_collect_hospital_all_channels_fail_returns_empty_list_not_exception():
    hospital = {"hospital_id": "gangnam_jstar", "hospital_name": "강남제이스타의원", "channels": _FULL_CHANNELS}

    def boom(_hospital):
        raise ValueError("실패")

    with patch("main.naver.collect_full", side_effect=boom), \
         patch("main.kakao.collect_full", side_effect=boom), \
         patch("main.google.collect_full", side_effect=boom):
        raw, errors = main.collect_hospital(hospital)
    assert raw == [], f"실제: {raw}"
    assert len(errors) == 3, f"실제: {errors}"
    print("PASS: test_collect_hospital_all_channels_fail_returns_empty_list_not_exception")


def test_collect_hospital_skips_channel_with_missing_url():
    # 경쟁병원처럼 일부 채널 URL을 못 찾은 경우, 그 채널은 호출조차 하지 않고
    # 조용히 건너뛴다(에러로 취급하지 않는다) — 도다나/고은미래의원의 실제 상황.
    hospital = {
        "hospital_id": "comp_dodana",
        "hospital_name": "도다나피부과의원",
        "channels": {
            "naver_place_url": "",
            "kakao_place_url": "https://place.map.kakao.com/262226489",
            "google_place_id": "0x3563758ac4f80f23:0x18d044a90c353c30",
        },
    }
    with patch("main.naver.collect_full") as mock_naver, \
         patch("main.kakao.collect_full", return_value=[{"channel": "카카오맵", "content": "좋아요"}]), \
         patch("main.google.collect_full", return_value=[{"channel": "구글", "content": "좋아요"}]):
        raw, errors = main.collect_hospital(hospital)
    mock_naver.assert_not_called()
    assert len(raw) == 2, f"실제: {raw}"
    assert errors == [], f"채널 미설정은 에러가 아니어야 함: {errors}"
    print("PASS: test_collect_hospital_skips_channel_with_missing_url")


def test_load_competitors_reads_config():
    competitors = main.load_competitors()
    ids = [c["hospital_id"] for c in competitors]
    assert "comp_audrey" in ids and "comp_dodana" in ids, f"실제: {ids}"
    # 2026-09-22 강남제이스타 원주시 22곳, 2026-10-13 자사 28곳으로 확장(병원당 최대 15곳)
    jstar = [c for c in competitors if "gangnam_jstar" in c["for_hospitals"]]
    assert len(jstar) == 22, f"실제: {len(jstar)}"
    assert all(c["for_hospitals"] for c in competitors), "모든 경쟁병원은 붙을 자사 병원이 있어야 함"
    assert len({c["hospital_id"] for c in competitors}) == len(competitors), "competitor_id 중복"
    dodana = next(c for c in competitors if c["hospital_id"] == "comp_dodana")
    assert dodana["hospital_name"] == "도다나피부과의원"
    # 빈 값(미확인)이거나, find_naver_place_ids.py로 채운 플레이스 URL 형식이어야 한다
    naver_url = dodana["channels"]["naver_place_url"]
    assert naver_url == "" or naver_url.startswith("https://m.place.naver.com/hospital/"), f"실제: {naver_url}"
    print("PASS: test_load_competitors_reads_config")


def test_run_routes_new_reviews_through_review_ingest():
    hospital = {"hospital_id": "h_a", "hospital_name": "가나다의원", "channels": _FULL_CHANNELS}
    existing = [{"hospital_id": "h_a", "channel": "네이버", "content": "기존"},
                {"hospital_id": "h_other", "channel": "네이버", "content": "다른 병원"}]
    raw = [{"channel": "네이버", "content": "새 리뷰"}]
    with patch.dict(os.environ, {"GOOGLE_SHEET_ID": "x", "GCP_SA_KEY": "{}"}), \
         patch("main.load_hospitals", return_value=[hospital]), \
         patch("main.load_competitors", return_value=[]), \
         patch("main.sheets_writer.connect", return_value="WS"), \
         patch("main.sheets_writer.ensure_header"), \
         patch("main.sheets_writer.read_existing_reviews", return_value=existing), \
         patch("main.collect_hospital", return_value=(raw, [])), \
         patch("main.review_ingest.ingest", return_value=1) as ingest:
        main.run()
    ingest.assert_called_once()
    worksheet, existing_arg, hospital_id, reviews, collected_at = ingest.call_args.args
    assert worksheet == "WS" and hospital_id == "h_a" and reviews == raw
    assert existing_arg == [existing[0]], "그 병원 행만 넘겨야 함"
    assert collected_at
    print("PASS: test_run_routes_new_reviews_through_review_ingest")


def _comps(n):
    return [{"hospital_id": f"comp_{i:03d}", "hospital_name": f"c{i}", "channels": {}} for i in range(n)]


def test_competitor_groups_cover_everyone_once_per_cycle():
    import datetime
    comps = _comps(330)
    n_groups = main.competitor_group_count(len(comps), capacity=80)
    assert n_groups == 5
    seen = []
    for d in range(n_groups):
        day = main.ROTATION_EPOCH + datetime.timedelta(days=d)
        group, picked = main.todays_competitors(comps, day, capacity=80)
        assert group == d
        assert len(picked) <= 80
        seen += [c["hospital_id"] for c in picked]
    assert sorted(seen) == sorted(c["hospital_id"] for c in comps), "한 주기 안에 모두 정확히 한 번"
    # 같은 날짜 → 같은 그룹(재현성), 주기가 돌면 다시 0그룹
    day = main.ROTATION_EPOCH + datetime.timedelta(days=n_groups)
    assert main.todays_competitors(comps, day, capacity=80)[0] == 0
    assert main.todays_competitors(comps, day, capacity=80) == main.todays_competitors(list(reversed(comps)), day, capacity=80)
    print("PASS: test_competitor_groups_cover_everyone_once_per_cycle")


def test_select_competitors_by_override():
    import datetime
    comps = _comps(10)
    day = main.ROTATION_EPOCH
    assert len(main.select_competitors(comps, day, "all", capacity=4)) == 10
    g1 = main.select_competitors(comps, day, "1", capacity=4)
    assert [c["hospital_id"] for c in g1] == ["comp_001", "comp_004", "comp_007"]
    assert main.select_competitors(comps, day, "", capacity=4) == main.todays_competitors(comps, day, capacity=4)[1]
    print("PASS: test_select_competitors_by_override")


def _run_with(hospitals, competitors, clock, group_env="all"):
    calls = []

    def fake_collect(h):
        calls.append(h["hospital_id"])
        return [], []

    with patch.dict(os.environ, {"GOOGLE_SHEET_ID": "x", "GCP_SA_KEY": "{}", "COMPETITOR_GROUP": group_env}), \
         patch("main.load_hospitals", return_value=hospitals), \
         patch("main.load_competitors", return_value=competitors), \
         patch("main.sheets_writer.connect", return_value="WS"), \
         patch("main.sheets_writer.ensure_header"), \
         patch("main.sheets_writer.read_existing_reviews", return_value=[]), \
         patch("main.collect_hospital", side_effect=fake_collect), \
         patch("main.review_ingest.ingest", return_value=0), \
         patch("main.time.monotonic", side_effect=clock):
        main.run()
    return calls


def test_run_stops_competitors_at_time_budget_but_always_collects_own():
    own = [{"hospital_id": "own_a", "hospital_name": "a", "channels": {}}]
    comps = _comps(5)
    # 경쟁병원 단계 시작 0초, 이후 매 확인마다 40분씩 흐른다고 가정 → 0, 40, 80분까지 수집, 120분에서 중단
    ticks = iter([0, 0, 40 * 60, 80 * 60, 120 * 60, 160 * 60, 200 * 60])
    calls = _run_with(own, comps, lambda: next(ticks))
    assert calls[0] == "own_a"
    assert calls[1:] == ["comp_000", "comp_001", "comp_002"], calls
    print("PASS: test_run_stops_competitors_at_time_budget_but_always_collects_own")


def test_run_continues_after_sheet_write_failure_and_reports_it():
    # 2026-10-22: 한 병원의 시트 쓰기 오류로 수집 전체가 멈춰 뒤 병원들이 빠졌다 → 그 병원만 실패로 남기고 계속
    hospitals = [{"hospital_id": h, "hospital_name": h, "channels": {}} for h in ("h_a", "h_b")]

    def fake_ingest(worksheet, existing, hospital_id, reviews, collected_at):
        if hospital_id == "h_a":
            raise RuntimeError("[409]: The operation was aborted.")
        return 0

    with patch.dict(os.environ, {"GOOGLE_SHEET_ID": "x", "GCP_SA_KEY": "{}", "COMPETITOR_GROUP": "all"}), \
         patch("main.load_hospitals", return_value=hospitals), \
         patch("main.load_competitors", return_value=[]), \
         patch("main.sheets_writer.connect", return_value="WS"), \
         patch("main.sheets_writer.ensure_header"), \
         patch("main.sheets_writer.read_existing_reviews", return_value=[]), \
         patch("main.collect_hospital", return_value=([], [])), \
         patch("main.review_ingest.ingest", side_effect=fake_ingest) as ingest:
        failed = main.run()
    assert ingest.call_count == 2, "h_a가 실패해도 h_b는 수집해야 함"
    assert failed == ["h_a"], f"실제: {failed}"
    print("PASS: test_run_continues_after_sheet_write_failure_and_reports_it")


if __name__ == "__main__":
    test_run_continues_after_sheet_write_failure_and_reports_it()
    test_competitor_groups_cover_everyone_once_per_cycle()
    test_select_competitors_by_override()
    test_run_stops_competitors_at_time_budget_but_always_collects_own()
    test_collect_hospital_aggregates_all_channels()
    test_collect_hospital_isolates_single_channel_failure()
    test_collect_hospital_all_channels_fail_returns_empty_list_not_exception()
    test_collect_hospital_skips_channel_with_missing_url()
    test_load_competitors_reads_config()
    test_run_routes_new_reviews_through_review_ingest()
