import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import build_meta


def test_build_assigns_competitors_by_for_hospitals():
    meta = {"h1": {"name": "가", "lat": 1, "lng": 2, "competitors": [{"id": "old"}]},
            "h2": {"name": "나", "lat": 3, "lng": 4}}
    config = {"competitors": [
        {"competitor_id": "c1", "name": "x", "lat": 1.1, "lng": 2.1, "for_hospitals": ["h1", "h2"]},
        {"competitor_id": "c2", "name": "y", "lat": 1.2, "lng": 2.2, "for_hospitals": ["h2"]},
    ]}
    out = build_meta.build(meta, config)
    assert [c["id"] for c in out["h1"]["competitors"]] == ["c1"]
    assert [c["id"] for c in out["h2"]["competitors"]] == ["c1", "c2"]
    assert out["h1"]["name"] == "가" and out["h2"]["lat"] == 3
    print("PASS: test_build_assigns_competitors_by_for_hospitals")


def test_committed_meta_matches_config():
    """저장소의 hospitals_meta.json이 competitors_config.json에서 생성된 그대로인지(어긋남 방지)."""
    meta = json.loads(build_meta.META_PATH.read_text(encoding="utf-8"))
    config = json.loads(build_meta.COMPETITORS_PATH.read_text(encoding="utf-8"))
    assert build_meta.build(meta, config) == meta, "python build_meta.py를 다시 실행하세요"
    ids = [c["competitor_id"] for c in config["competitors"]]
    assert len(ids) == len(set(ids)), "competitor_id 중복"
    assert all(c.get("for_hospitals") for c in config["competitors"]), "for_hospitals 빈 경쟁병원"
    print("PASS: test_committed_meta_matches_config")


if __name__ == "__main__":
    test_build_assigns_competitors_by_for_hospitals()
    test_committed_meta_matches_config()
