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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
