"""매일 13:50 Windows 작업 스케줄러가 실행하는 검수 작업(2026-10-14 사용자 결정).

1. 오늘 13시 GitHub Actions 수집이 끝날 때까지 기다린다(10분 간격, 16:30까지 — 넘기면 그대로 진행).
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
RUNS_API = "https://api.github.com/repos/sema-cs/review-integration-dashboard/actions/runs?event=schedule&per_page=3"
KST = datetime.timezone(datetime.timedelta(hours=9))

RECLASSIFY_LIMIT = 300
RECLASSIFY_MAX_ROUNDS = 4
WAIT_DEADLINE = datetime.time(16, 30)
# 이 시각 전에 시작됐으면(PC가 꺼져 놓친 실행을 아침에 따라잡는 경우) 오늘 수집을 기다리지 않는다.
# 2026-10-19 실측: 08:54 따라잡기 실행이 수집 대기만 하다 5시간 상한(13:54)에 강제 종료됐고,
# 13:50 정규 실행은 "이미 실행 중"(IgnoreNew)이라 무시돼 그날 재분류가 0건이었다.
# 아침 실행은 쌓인 분량만 처리하고 끝나며, 오늘 수집분은 13:50 정규 실행이 처리한다.
COLLECTION_START = datetime.time(13, 0)
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


def should_wait_for_collection(now_time):
    return now_time >= COLLECTION_START


def wait_for_collection(today):
    if not should_wait_for_collection(datetime.datetime.now(KST).time()):
        log("13시 전 실행(놓친 실행 따라잡기) → 수집 대기 없이 쌓인 분량부터 처리")
        return
    while True:
        try:
            with urllib.request.urlopen(RUNS_API, timeout=30) as res:
                runs = json.load(res)["workflow_runs"]
            todays = [r for r in runs if r.get("run_started_at") and datetime.datetime.fromisoformat(
                r["run_started_at"].replace("Z", "+00:00")).astimezone(KST).date() == today]
            if todays and todays[0]["status"] == "completed":
                log(f"오늘 수집 완료 확인: {todays[0]['conclusion']} ({todays[0]['updated_at']})")
                return
            state = todays[0]["status"] if todays else "아직 시작 전"
        except Exception as e:  # 네트워크 일시 오류는 기다렸다 재시도
            state = f"조회 실패: {e}"
        if datetime.datetime.now(KST).time() >= WAIT_DEADLINE:
            log(f"16:30까지 수집 완료를 확인 못 함({state}) → 그대로 진행")
            return
        log(f"수집 대기: {state} → 10분 후 재확인")
        time.sleep(600)


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
        wait_for_collection(today)
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
