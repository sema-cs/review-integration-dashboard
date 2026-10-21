"""매일 13:50·19:00 Windows 작업 스케줄러가 실행하는 검수 작업(2026-10-14 사용자 결정, 19:00은 2026-10-20 추가).

1. 오늘 GitHub Actions 수집(12:17 예약) 상태를 로그에 남기기만 하고 기다리지 않는다(2026-10-20 —
   예약 수집이 몇 시간씩 늦는 날이 잦음). 늦게 들어온 리뷰는 19:00 실행이나 다음 날 실행이 처리한다.
   13시가 지났는데 오늘 GitHub 수집(예약·수동)이 아예 없으면 이 PC에서 main.py로 직접 수집한다
   (2026-10-21 사용자 결정 — 10-20·21 예약 수집이 생성조차 안 됨). 자사 전부 + 오늘 경쟁병원 순번.
2. 재분류: reclassify.py --export(회당 300건) → Claude Code(`claude -p`)가 [재분류결과].json 작성
   → reclassify.py --apply. 남은 후보가 있으면 최대 4회 반복, 나머지는 다음 날로 넘어간다.
3. 주간 특이사항: weekly_notes.py --due가 참(월요일, 또는 월요일을 놓쳐 7일 이상 지남)이면
   --export → Claude가 [주간결과].json 작성 → --apply → weekly_notes.json만 커밋·push.

판단(분류·요약)은 이미 구독 중인 Claude Code가 하고, 이 스크립트는 파일·시트·git 처리만 한다.
Claude에게는 Read/Write 계열 도구만 허용한다(명령 실행 불가). 로그: scheduled_logs/YYYY-MM-DD.log
"""

import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "scheduled_logs"
RECLASSIFY_WORK = ROOT / "reclassify_work"
WEEKLY_WORK = ROOT / "weekly_work"
# 예약·수동 실행 모두 본다(수동 실행이 있는 날은 PC 보완 수집을 하지 않는다)
RUNS_API = ("https://api.github.com/repos/sema-cs/review-integration-dashboard"
            "/actions/workflows/daily_collect.yml/runs?per_page=5")
KST = datetime.timezone(datetime.timedelta(hours=9))

LOCAL_COLLECT_AFTER_HOUR = 13  # 이보다 이르면(놓친 실행 따라잡기 등) 12:17 예약을 아직 기다린다
LOCAL_COLLECT_TIMEOUT = 3 * 60 * 60  # Actions timeout-minutes와 같음(자사 ~30분 + 경쟁 90분 예산)
LOCAL_COLLECT_MARK = LOG_DIR / "local_collection.json"
NO_RUN_TODAY = "아직 시작 전"

RECLASSIFY_LIMIT = 300
RECLASSIFY_MAX_ROUNDS = 4
CLAUDE_TIMEOUT = 60 * 60
CLAUDE_TOOLS = ["--allowedTools", "Read", "Write", "Edit", "Glob", "Grep",
                "--disallowedTools", "Bash", "PowerShell", "WebFetch", "WebSearch"]

RECLASSIFY_PROMPT = (
    "자동 실행된 재분류 작업이다. 사람에게 질문하지 말고 끝까지 처리한다. 명령은 실행하지 않는다.\n"
    "reclassify_work/[재분류요청].json을 읽고, 이 폴더 CLAUDE.md의 `[재분류결과].json 형식`, `판단 기준`, "
    "`주요 포인트 작성 규칙`, `심각도 판정 기준`을 그대로 따라 reclassify_work/[재분류결과].json을 작성하라. "
    "모든 candidates의 id에 결과를 하나씩 쓴다. 긴 리뷰도 끝까지 읽고 판단한다(결론이 끝에 있다). "
    "key_points는 existing_key_points의 같은 감성 표현을 우선 재사용한다. "
    "tasks에 severity가 있고 최종 판단이 부정이면 severity를 반드시 쓴다. 다 쓰면 한 줄로 건수만 보고하라."
)
WEEKLY_PROMPT = (
    "자동 실행된 주간 특이사항 작성 작업이다. 사람에게 질문하지 말고 끝까지 처리한다. 명령은 실행하지 않는다.\n"
    "weekly_work/[주간요청].json을 읽고, 이 폴더 CLAUDE.md의 `주간 특이사항 갱신` 규칙에 따라 "
    "weekly_work/[주간결과].json을 {\"notes\": {hospital_id: {\"flag\": \"none|watch|risk\", \"summary\": \"1문장\"}}} "
    "형식으로 작성하라. 요청 파일의 모든 병원을 빠짐없이 쓴다. 리뷰 본문에 없는 내용은 쓰지 않는다. "
    "count가 0이면 \"최근 7일 새 리뷰가 없습니다(마지막 리뷰 M월 D일).\"처럼 사실만 쓰고(last_review_date가 "
    "없으면 마지막 리뷰 부분은 빼고) flag는 none. 환자 실명·연락처는 쓰지 않는다. 다 쓰면 한 줄로 보고하라."
)

_log_file = None


def log(msg):
    line = f"[{datetime.datetime.now(KST):%H:%M:%S}] {msg}"
    print(line, flush=True)
    if _log_file:
        with open(_log_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def run(args, timeout=1800, check=True):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, env=env)
    out = (proc.stdout + proc.stderr).strip()
    log(f"$ {' '.join(str(a) for a in args[:4])}... → exit {proc.returncode}" + (f"\n{out[-2000:]}" if out else ""))
    if check and proc.returncode != 0:
        raise RuntimeError(f"실패: {args[:3]}")
    return proc


def claude_exe():
    found = shutil.which("claude")
    return found or str(Path.home() / ".local" / "bin" / "claude.exe")


def ask_claude(prompt):
    run([claude_exe(), "-p", prompt, *CLAUDE_TOOLS], timeout=CLAUDE_TIMEOUT)


def collection_status(today):
    """오늘 예약 수집(GitHub Actions) 상태를 한 번 조회해 문자열로 돌려준다. 기다리지 않는다 —
    2026-10-17~20 예약 수집이 4~5시간씩 늦어져 검수가 대기만 하다 끝났다. 늦게 들어온 리뷰는
    같은 날 19:00 실행이나 다음 날 실행이 처리한다."""
    try:
        with urllib.request.urlopen(RUNS_API, timeout=30) as res:
            runs = json.load(res)["workflow_runs"]
    except Exception as e:  # 상태 기록용일 뿐이라 실패해도 검수는 진행한다
        return f"조회 실패: {e}"
    def started(r):
        ts = r.get("run_started_at") or r.get("created_at")  # 대기(queued) 중인 실행도 오늘 실행으로 센다
        return ts and datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(KST).date()

    todays = [r for r in runs if started(r) == today]
    if not todays:
        return NO_RUN_TODAY
    if todays[0]["status"] == "completed":
        return f"completed: {todays[0]['conclusion']} ({todays[0]['updated_at']})"
    return todays[0]["status"]


def local_collection_step(today, collection, now):
    """GitHub 수집이 오늘 아예 없을 때만 이 PC에서 main.py를 돌린다. 조회 실패면 하지 않는다
    (GitHub가 돌고 있을 수 있어 중복 적재 위험). 같은 날 이미 성공했으면 19:00 실행은 건너뛴다."""
    if collection != NO_RUN_TODAY:
        return "하지 않음(GitHub 수집 있음)" if not collection.startswith("조회 실패") else "하지 않음(상태 조회 실패)"
    if now.hour < LOCAL_COLLECT_AFTER_HOUR:
        return f"하지 않음({LOCAL_COLLECT_AFTER_HOUR}시 전 — 예약 수집 대기)"
    try:
        mark = json.loads(LOCAL_COLLECT_MARK.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        mark = {}
    if mark.get("date") == today.isoformat() and mark.get("result") == "success":
        return "하지 않음(오늘 PC 수집 완료됨)"
    log("오늘 GitHub 수집이 없어 이 PC에서 직접 수집합니다(자사 전부 + 오늘 경쟁병원 순번)")
    started_at = datetime.datetime.now(KST).isoformat(timespec="seconds")
    try:
        code = run([sys.executable, "main.py"], timeout=LOCAL_COLLECT_TIMEOUT, check=False).returncode
        result = "success" if code == 0 else f"failed: exit {code}"
    except subprocess.TimeoutExpired:
        result = "failed: timeout"
    LOCAL_COLLECT_MARK.write_text(json.dumps(
        {"date": today.isoformat(), "started_at": started_at, "result": result,
         "finished_at": datetime.datetime.now(KST).isoformat(timespec="seconds")},
        ensure_ascii=False, indent=1), encoding="utf-8")
    return f"PC 수집 {result}"  # 실패해도 검수는 이미 쌓인 리뷰로 계속 진행한다


def archive(work_dir, names, tag):
    stamp = datetime.datetime.now(KST).strftime("%Y-%m-%d_%H%M")
    for name in names:
        path = work_dir / name
        if path.exists():
            path.rename(work_dir / f"{path.stem}_{tag}_{stamp}{path.suffix}")


def reclassify_rounds():
    names = ("[재분류요청].json", "[재분류결과].json")
    archive(RECLASSIFY_WORK, names, "미반영")  # 이전 실행이 중간에 멈춘 흔적은 치워 둔다(다음 export와 섞이지 않게)
    applied = 0
    for n in range(1, RECLASSIFY_MAX_ROUNDS + 1):
        run([sys.executable, "reclassify.py", "--export", "--limit", str(RECLASSIFY_LIMIT)], timeout=900)
        if not (RECLASSIFY_WORK / names[0]).exists():
            log(f"재분류 대상 없음(회차 {n})")
            break
        ask_claude(RECLASSIFY_PROMPT)
        if not (RECLASSIFY_WORK / names[1]).exists():
            raise RuntimeError("Claude가 [재분류결과].json을 만들지 않음")
        proc = run([sys.executable, "reclassify.py", "--apply"], timeout=900)
        m = re.search(r"(\d+)건 반영 완료", proc.stdout)
        applied += int(m[1]) if m else 0
        archive(RECLASSIFY_WORK, names, "자동")
    return applied


def weekly_notes_step():
    if run([sys.executable, "weekly_notes.py", "--due"], check=False).returncode != 0:
        log("주간 특이사항: 이번 실행은 차례 아님")
        return False
    names = ("[주간요청].json", "[주간결과].json")
    archive(WEEKLY_WORK, names, "미반영")
    run([sys.executable, "weekly_notes.py", "--export"], timeout=900)
    ask_claude(WEEKLY_PROMPT)
    run([sys.executable, "weekly_notes.py", "--apply"])
    archive(WEEKLY_WORK, names, "자동")
    notes = json.loads((ROOT / "weekly_notes.json").read_text(encoding="utf-8"))
    msg = f"주간 특이사항 자동 갱신 {notes['period_start']} ~ {notes['period_end']}"
    run(["git", "add", "weekly_notes.json"])
    if run(["git", "diff", "--cached", "--quiet"], check=False).returncode == 0:
        log("weekly_notes.json 변경 없음")
        return True
    run(["git", "commit", "-m", msg, "--", "weekly_notes.json"])
    if run(["git", "push", "origin", "HEAD"], check=False, timeout=300).returncode != 0:
        run(["git", "pull", "--rebase", "origin", "main"], timeout=300)
        run(["git", "push", "origin", "HEAD"], timeout=300)
    return True


def main():
    global _log_file
    today = datetime.datetime.now(KST).date()
    LOG_DIR.mkdir(exist_ok=True)
    _log_file = LOG_DIR / f"{today.isoformat()}.log"
    status = {"date": today.isoformat(), "started_at": datetime.datetime.now(KST).isoformat(timespec="seconds")}
    try:
        log("=== 일일 검수 작업 시작 ===")
        status["collection"] = collection_status(today)
        log(f"오늘 수집 상태: {status['collection']} (기다리지 않고 진행)")
        status["local_collection"] = local_collection_step(today, status["collection"], datetime.datetime.now(KST))
        log(f"PC 보완 수집: {status['local_collection']}")
        status["reclassified"] = reclassify_rounds()
        status["weekly_notes"] = weekly_notes_step()
        status["result"] = "success"
    except Exception as e:
        status["result"] = f"failed: {e}"
        log(f"!! 중단: {e}")
    status["finished_at"] = datetime.datetime.now(KST).isoformat(timespec="seconds")
    (LOG_DIR / "last_run.json").write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"=== 종료: {status} ===")
    return 0 if status["result"] == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
