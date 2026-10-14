"""주간 특이사항(weekly_notes.json) — 결정적 처리만 맡는다. 요약 문장은 Claude Code가 쓴다.

python weekly_notes.py --export     → weekly_work/[주간요청].json (자사 병원별 최근 7일 리뷰, 오늘 포함)
Claude가 읽고 weekly_work/[주간결과].json 작성 ({"notes": {hospital_id: {flag, summary}}})
python weekly_notes.py --apply      → 검증 후 weekly_notes.json 저장(count는 요청 파일 값을 쓴다)
python weekly_notes.py --due        → 오늘 작성할 차례면 exit 0, 아니면 exit 1 (월요일 또는 마지막 작성이 7일 이상 지남)

작성 규칙은 폴더 CLAUDE.md "주간 특이사항 갱신" 참고. 2026-10-14 사용자 결정으로 매일 13:50
작업 스케줄러(scheduled/daily_job.ps1)가 월요일마다 실행한다.
"""

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

import reclassify

ROOT = Path(__file__).resolve().parent
WORK_DIR = ROOT / "weekly_work"
META_PATH = ROOT / "hospitals_meta.json"
NOTES_PATH = ROOT / "weekly_notes.json"
REQUEST_FILENAME = "[주간요청].json"
RESULT_FILENAME = "[주간결과].json"
FLAGS = ("none", "watch", "risk")
_KST = datetime.timezone(datetime.timedelta(hours=9))


def build_request(rows, meta, today):
    start = today - datetime.timedelta(days=6)
    hospitals = {}
    for hid, info in meta.items():
        dated = [(reclassify.review_date(r), r) for r in rows if r.get("hospital_id") == hid]
        dated = [(d, r) for d, r in dated if d is not None and d <= today]
        recent = sorted(((d, r) for d, r in dated if d >= start), key=lambda x: x[0])
        hospitals[hid] = {
            "name": info.get("name", hid),
            "count": len(recent),
            "last_review_date": max(d for d, _ in dated).isoformat() if dated else None,
            "reviews": [{"date": d.isoformat(), "channel": r.get("channel"), "sentiment": r.get("sentiment"),
                         "key_points": r.get("key_points") or "", "content": str(r.get("content") or "")}
                        for d, r in recent],
        }
    return {"period_start": start.isoformat(), "period_end": today.isoformat(), "hospitals": hospitals}


def build_notes(request, result, generated_at):
    notes_in = (result or {}).get("notes") or {}
    notes = {}
    for hid, info in request["hospitals"].items():
        note = notes_in.get(hid)
        if not isinstance(note, dict):
            raise ValueError(f"{hid}: 결과 누락")
        if note.get("flag") not in FLAGS:
            raise ValueError(f"{hid}: flag 값 {note.get('flag')!r}")
        summary = str(note.get("summary") or "").strip()
        if not summary:
            raise ValueError(f"{hid}: summary 비어 있음")
        notes[hid] = {"flag": note["flag"], "count": info["count"], "summary": summary}
    return {"generated_at": generated_at, "period_start": request["period_start"],
            "period_end": request["period_end"], "notes": notes}


def is_due(today, current_notes):
    """월요일이면 작성. 월요일을 놓쳤으면(마지막 기준일이 7일 이상 전) 다음 실행에서 이월 작성."""
    end = (current_notes or {}).get("period_end")
    if not end:
        return True
    last = datetime.date.fromisoformat(end)
    if last >= today:
        return False
    return today.weekday() == 0 or (today - last).days >= 7


def export_request(rows, meta, today, work_dir):
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    path = work_dir / REQUEST_FILENAME
    path.write_text(json.dumps(build_request(rows, meta, today), ensure_ascii=False, indent=1), encoding="utf-8")
    return str(path)


def apply_result(work_dir, notes_path):
    work_dir = Path(work_dir)
    request = json.loads((work_dir / REQUEST_FILENAME).read_text(encoding="utf-8"))
    result = json.loads((work_dir / RESULT_FILENAME).read_text(encoding="utf-8"))
    notes = build_notes(request, result, datetime.datetime.now(_KST).isoformat(timespec="seconds"))
    Path(notes_path).write_text(json.dumps(notes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return notes


def _load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="주간 특이사항 요청/반영")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--export", action="store_true")
    group.add_argument("--apply", action="store_true")
    group.add_argument("--due", action="store_true")
    args = parser.parse_args()
    today = datetime.datetime.now(_KST).date()

    if args.due:
        sys.exit(0 if is_due(today, _load_json(NOTES_PATH)) else 1)
    if args.export:
        import sheets_writer
        ws = sheets_writer.connect(os.environ["GOOGLE_SHEET_ID"], os.environ["GCP_SA_KEY"])
        rows = sheets_writer.read_existing_reviews(ws)
        path = export_request(rows, _load_json(META_PATH), today, WORK_DIR)
        print(f"주간 특이사항 요청 -> {path}")
    else:
        notes = apply_result(WORK_DIR, NOTES_PATH)
        print(f"weekly_notes.json 저장: {notes['period_start']} ~ {notes['period_end']}, {len(notes['notes'])}곳")
