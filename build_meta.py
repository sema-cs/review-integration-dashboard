"""config/competitors_config.json → hospitals_meta.json의 병원별 `competitors` 배열을 다시 만든다.

경쟁병원은 `for_hospitals`에 적힌 자사 병원 화면에만 보인다(2026-10-13 다병원 확장).
자사 병원 키·이름·좌표 등 나머지 필드는 건드리지 않는다.

사용: python build_meta.py
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
META_PATH = ROOT / "hospitals_meta.json"
COMPETITORS_PATH = ROOT / "config" / "competitors_config.json"


def build(meta, competitors_config):
    """순수 함수: 새 meta dict를 돌려준다. 경쟁병원 순서는 config 순서를 따른다."""
    out = {}
    for hid, info in meta.items():
        if not isinstance(info, dict):
            out[hid] = info
            continue
        comps = [
            {"id": c["competitor_id"], "name": c["name"], "lat": c["lat"], "lng": c["lng"]}
            for c in competitors_config.get("competitors", [])
            if hid in c.get("for_hospitals", [])
        ]
        out[hid] = dict(info, competitors=comps)
    return out


def dumps(meta):
    """기존 파일 형식 유지: 병원은 들여쓰기 2칸, 경쟁병원은 한 줄에 하나."""
    def one_line(obj):
        return "{ " + ", ".join(f"{json.dumps(k, ensure_ascii=False)}: {json.dumps(v, ensure_ascii=False)}"
                                for k, v in obj.items()) + " }"

    lines = ["{"]
    hids = list(meta)
    for i, hid in enumerate(hids):
        info = meta[hid]
        fields = []
        for k, v in info.items():
            if k == "competitors":
                if v:
                    inner = ",\n".join("      " + one_line(c) for c in v)
                    fields.append(f'    "competitors": [\n{inner}\n    ]')
                else:
                    fields.append('    "competitors": []')
            else:
                fields.append(f"    {json.dumps(k, ensure_ascii=False)}: {json.dumps(v, ensure_ascii=False)}")
        lines.append(f"  {json.dumps(hid, ensure_ascii=False)}: {{\n" + ",\n".join(fields) + "\n  }"
                     + ("," if i < len(hids) - 1 else ""))
    lines.append("}")
    return "\n".join(lines) + "\n"


def main():
    meta = json.loads(META_PATH.read_text(encoding="utf-8"))
    config = json.loads(COMPETITORS_PATH.read_text(encoding="utf-8"))
    unknown = sorted({h for c in config["competitors"] for h in c.get("for_hospitals", [])} - set(meta))
    if unknown:
        raise SystemExit(f"for_hospitals에 hospitals_meta.json에 없는 병원 ID가 있습니다: {unknown}")
    new = build(meta, config)
    META_PATH.write_text(dumps(new), encoding="utf-8")
    for hid, info in new.items():
        print(f"{hid}: 경쟁병원 {len(info.get('competitors', []))}곳")


if __name__ == "__main__":
    main()
