"""워크플로 예약 실행의 첫 단계: 오늘(KST) 다른 수집 실행이 이미 있으면 `skip=true`를 출력한다.

2026-10-22부터 주 실행은 PC가 12:17에 수동 실행(workflow_dispatch)으로 시작하고, 예약 실행은 예비다.
예약 실행이 몇 시간 늦게 들어와도 같은 날 두 번 수집하지 않게 한다. 실패·취소된 실행은 세지 않는다
(그날 수집이 안 된 것이므로 예비 실행이 수집한다).

사용: gh api .../runs?per_page=10 | python3 scheduled/other_runs_today.py <이 실행의 run_id> >> $GITHUB_OUTPUT
"""

import datetime
import json
import sys

KST = datetime.timezone(datetime.timedelta(hours=9))


def count_other_runs_today(runs, own_run_id, today):
    n = 0
    for r in runs:
        if str(r["id"]) == str(own_run_id) or r.get("conclusion") in ("failure", "cancelled"):
            continue
        created = datetime.datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")).astimezone(KST).date()
        if created == today:
            n += 1
    return n


def main():
    runs = json.load(sys.stdin)["workflow_runs"]
    today = datetime.datetime.now(KST).date()
    n = count_other_runs_today(runs, sys.argv[1], today)
    print(f"오늘({today}) 다른 수집 실행 {n}건", file=sys.stderr)
    if n:
        print("skip=true")


if __name__ == "__main__":
    main()
