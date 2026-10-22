import datetime
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "scheduled"))
import daily_job


def _fake_runs(runs):
    def fake_urlopen(url, timeout=None):
        return io.BytesIO(json.dumps({"workflow_runs": runs}).encode("utf-8"))
    return fake_urlopen


def _no_sleep(seconds):
    raise AssertionError(f"수집을 기다리면 안 됨(sleep {seconds}초)")


def test_collection_status_never_waits():
    # 2026-10-17~20 GitHub 예약 수집이 4~5시간씩 늦어져 검수가 16:30까지 대기만 했다 →
    # 검수는 수집을 기다리지 않고 상태만 기록한 뒤 바로 진행한다(2026-10-20 사용자 결정).
    today = datetime.date(2026, 10, 20)
    orig_urlopen, orig_sleep = daily_job.urllib.request.urlopen, daily_job.time.sleep
    daily_job.time.sleep = _no_sleep
    try:
        daily_job.urllib.request.urlopen = _fake_runs([])
        assert daily_job.collection_status(today) == "아직 시작 전"
        daily_job.urllib.request.urlopen = _fake_runs([
            {"run_started_at": "2026-10-20T08:31:00Z", "status": "in_progress", "conclusion": None, "updated_at": ""}])
        assert daily_job.collection_status(today) == "in_progress"
        daily_job.urllib.request.urlopen = _fake_runs([
            {"run_started_at": "2026-10-20T03:20:00Z", "status": "completed", "conclusion": "success",
             "updated_at": "2026-10-20T04:30:00Z"}])
        assert daily_job.collection_status(today) == "completed: success (2026-10-20T04:30:00Z)"
    finally:
        daily_job.urllib.request.urlopen, daily_job.time.sleep = orig_urlopen, orig_sleep
    print("PASS: test_collection_status_never_waits")


def test_collection_status_network_error_is_reported_not_raised():
    def boom(url, timeout=None):
        raise OSError("network down")
    orig = daily_job.urllib.request.urlopen
    daily_job.urllib.request.urlopen = boom
    try:
        assert daily_job.collection_status(datetime.date(2026, 10, 20)).startswith("조회 실패")
    finally:
        daily_job.urllib.request.urlopen = orig
    print("PASS: test_collection_status_network_error_is_reported_not_raised")


def test_collection_status_counts_queued_manual_run():
    # 수동 실행(workflow_dispatch)이 대기 중이면 run_started_at이 없어도 오늘 실행으로 센다
    orig = daily_job.urllib.request.urlopen
    daily_job.urllib.request.urlopen = _fake_runs([
        {"created_at": "2026-10-21T06:38:09Z", "status": "queued", "conclusion": None, "updated_at": ""}])
    try:
        assert daily_job.collection_status(datetime.date(2026, 10, 21)) == "queued"
    finally:
        daily_job.urllib.request.urlopen = orig
    print("PASS: test_collection_status_counts_queued_manual_run")


class _FakeProc:
    def __init__(self, returncode):
        self.returncode = returncode


def test_local_collection_only_when_github_missed():
    # 2026-10-20·21 예약 수집이 생성조차 안 됨 → 13시 이후 GitHub 실행이 없으면 PC에서 main.py 실행
    import tempfile
    today = datetime.date(2026, 10, 21)
    at = lambda h: datetime.datetime(2026, 10, 21, h, 50, tzinfo=daily_job.KST)
    calls = []

    def fake_run(args, timeout=1800, check=True):
        calls.append(args)
        return _FakeProc(0)

    orig_run, orig_mark = daily_job.run, daily_job.LOCAL_COLLECT_MARK
    with tempfile.TemporaryDirectory() as tmp:
        daily_job.run = fake_run
        daily_job.LOCAL_COLLECT_MARK = Path(tmp) / "local_collection.json"
        try:
            assert daily_job.local_collection_step(today, "in_progress", at(13)).startswith("하지 않음")
            assert daily_job.local_collection_step(today, "조회 실패: x", at(13)).startswith("하지 않음")
            assert daily_job.local_collection_step(today, daily_job.NO_RUN_TODAY, at(8)).startswith("하지 않음")
            assert calls == []
            assert daily_job.local_collection_step(today, daily_job.NO_RUN_TODAY, at(13)) == "PC 수집 success"
            assert calls[0][1] == "main.py"
            # 같은 날 19:00 실행은 다시 수집하지 않는다
            assert daily_job.local_collection_step(today, daily_job.NO_RUN_TODAY, at(19)).startswith("하지 않음")
            assert len(calls) == 1
        finally:
            daily_job.run, daily_job.LOCAL_COLLECT_MARK = orig_run, orig_mark
    print("PASS: test_local_collection_only_when_github_missed")


def test_local_collection_retries_after_failure():
    import tempfile
    today = datetime.date(2026, 10, 21)
    codes = [1, 0]
    orig_run, orig_mark = daily_job.run, daily_job.LOCAL_COLLECT_MARK
    with tempfile.TemporaryDirectory() as tmp:
        daily_job.run = lambda args, timeout=1800, check=True: _FakeProc(codes.pop(0))
        daily_job.LOCAL_COLLECT_MARK = Path(tmp) / "local_collection.json"
        try:
            now = datetime.datetime(2026, 10, 21, 13, 50, tzinfo=daily_job.KST)
            assert daily_job.local_collection_step(today, daily_job.NO_RUN_TODAY, now) == "PC 수집 failed: exit 1"
            assert daily_job.local_collection_step(today, daily_job.NO_RUN_TODAY, now) == "PC 수집 success"
        finally:
            daily_job.run, daily_job.LOCAL_COLLECT_MARK = orig_run, orig_mark
    print("PASS: test_local_collection_retries_after_failure")


def test_archive_same_minute_does_not_collide():
    # 2026-10-22 08:46 회차가 1분 안에 끝나 같은 보관 이름이 이미 있어 WinError 183로 실행 전체가 실패 기록됨
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        for _ in range(3):
            (d / "[재분류요청].json").write_text("{}", encoding="utf-8")
            daily_job.archive(d, ("[재분류요청].json",), "자동")
        assert not (d / "[재분류요청].json").exists()
        assert len(list(d.glob("*_자동_*"))) == 3
    print("PASS: test_archive_same_minute_does_not_collide")


def test_trigger_dispatches_only_when_nothing_ran_today():
    # 2026-10-22: 예약 실행이 늦거나 빠져 PC가 12:17에 workflow_dispatch로 수집을 시작한다
    import tempfile
    import trigger_collection as tc
    sent = []
    orig = (daily_job.collection_status, tc.dispatch, daily_job.LOCAL_COLLECT_MARK, daily_job.LOG_DIR,
            tc.os.environ.get(tc.TOKEN_ENV))
    with tempfile.TemporaryDirectory() as tmp:
        daily_job.LOG_DIR = Path(tmp)
        daily_job.LOCAL_COLLECT_MARK = Path(tmp) / "local_collection.json"
        tc.dispatch = lambda token: sent.append(token) or 204
        try:
            tc.os.environ[tc.TOKEN_ENV] = "t"
            daily_job.collection_status = lambda today: "in_progress"
            assert tc.main() == 0 and sent == []
            daily_job.collection_status = lambda today: daily_job.NO_RUN_TODAY
            assert tc.main() == 0 and sent == ["t"]
            tc.os.environ.pop(tc.TOKEN_ENV)
            assert tc.main() == 1 and sent == ["t"]  # 토큰 없으면 실패로 끝내고 13:50 PC 수집에 맡긴다
        finally:
            daily_job.collection_status, tc.dispatch, daily_job.LOCAL_COLLECT_MARK, daily_job.LOG_DIR, tok = orig
            if tok is None:
                tc.os.environ.pop(tc.TOKEN_ENV, None)
            else:
                tc.os.environ[tc.TOKEN_ENV] = tok
    print("PASS: test_trigger_dispatches_only_when_nothing_ran_today")


def test_other_runs_today_ignores_self_failed_and_yesterday():
    import other_runs_today as o
    today = datetime.date(2026, 10, 22)
    runs = [
        {"id": 9, "created_at": "2026-10-22T05:00:00Z", "conclusion": None},       # 자기 자신
        {"id": 8, "created_at": "2026-10-22T03:17:00Z", "conclusion": "failure"},  # 실패는 안 셈
        {"id": 7, "created_at": "2026-10-21T08:39:30Z", "conclusion": "success"},  # 어제 17:39 KST
        {"id": 6, "created_at": "2026-10-21T16:00:00Z", "conclusion": "success"},  # 오늘 01:00 KST
    ]
    assert o.count_other_runs_today(runs, 9, today) == 1
    assert o.count_other_runs_today(runs[:3], "9", today) == 0
    print("PASS: test_other_runs_today_ignores_self_failed_and_yesterday")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
