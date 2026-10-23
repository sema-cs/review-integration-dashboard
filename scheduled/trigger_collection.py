"""매일 11:00(2026-10-23 12:17에서 앞당김) Windows 작업 스케줄러가 GitHub Actions 수집을 수동 실행(workflow_dispatch)으로 시작한다.

2026-10-17부터 GitHub 예약(schedule) 실행이 4~5시간씩 늦거나 아예 빠졌다(10-20 누락, 10-22 13:52까지 미생성).
GitHub 문서상 예약 실행은 부하가 높으면 늦어지거나 빠질 수 있고, 같은 시기 커뮤니티에도 같은 보고가
잇따랐다. 수동 실행은 바로 시작되므로 PC가 실행 버튼을 대신 누른다(2026-10-22 사용자 결정).
수집 자체는 지금처럼 GitHub 서버에서 돈다. 워크플로의 예약 실행은 예비로 남겨 두고, 오늘 이미 실행이
있으면 워크플로가 스스로 건너뛴다.

- 토큰: 사용자 환경변수 REVIEW_DISPATCH_TOKEN (sema-cs/review-integration-dashboard 저장소의
  Actions 읽기·쓰기 권한만 가진 fine-grained 토큰). 저장소·파일에는 저장하지 않는다.
- 오늘 GitHub 실행(예약·수동)이 이미 있거나 PC 보완 수집이 성공했으면 아무것도 하지 않는다.
- 실패해도 13:50 daily_job.py가 GitHub 실행이 없음을 보고 PC에서 직접 수집한다.
"""

import datetime
import json
import os
import sys
import urllib.request

import daily_job

DISPATCH_API = ("https://api.github.com/repos/sema-cs/review-integration-dashboard"
                "/actions/workflows/daily_collect.yml/dispatches")
TOKEN_ENV = "REVIEW_DISPATCH_TOKEN"


def local_collected_today(today):
    try:
        mark = json.loads(daily_job.LOCAL_COLLECT_MARK.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return mark.get("date") == today.isoformat() and mark.get("result") == "success"


def dispatch(token):
    req = urllib.request.Request(
        DISPATCH_API, method="POST", data=json.dumps({"ref": "main"}).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.urlopen(req, timeout=30) as res:
        return res.status  # 성공하면 204


def main():
    today = datetime.datetime.now(daily_job.KST).date()
    daily_job.LOG_DIR.mkdir(exist_ok=True)
    daily_job._log_file = daily_job.LOG_DIR / f"{today.isoformat()}.log"
    daily_job.log("=== 수집 실행 요청(workflow_dispatch) ===")
    status = daily_job.collection_status(today)
    if status != daily_job.NO_RUN_TODAY:
        daily_job.log(f"요청 안 함: 오늘 GitHub 수집 상태 = {status}")
        return 0
    if local_collected_today(today):
        daily_job.log("요청 안 함: 오늘 PC 보완 수집이 이미 성공함")
        return 0
    token = os.environ.get(TOKEN_ENV, "").strip()
    if not token:
        daily_job.log(f"!! 요청 실패: 환경변수 {TOKEN_ENV}가 없음 — 13:50 PC 보완 수집이 대신함")
        return 1
    try:
        code = dispatch(token)
    except Exception as e:  # 401(토큰 만료·권한 부족) 등. 토큰 값은 로그에 남기지 않는다
        daily_job.log(f"!! 요청 실패: {e} — 13:50 PC 보완 수집이 대신함")
        return 1
    daily_job.log(f"수집 실행 요청 완료(HTTP {code})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
