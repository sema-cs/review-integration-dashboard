import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "scheduled"))
import daily_job


def test_morning_catch_up_run_does_not_wait():
    # 2026-10-19: 08:54 따라잡기 실행이 수집 대기만 하다 5시간 상한에 걸려 종료됐다
    assert not daily_job.should_wait_for_collection(datetime.time(8, 54))
    assert not daily_job.should_wait_for_collection(datetime.time(12, 59))


def test_regular_run_waits_for_todays_collection():
    assert daily_job.should_wait_for_collection(datetime.time(13, 0))
    assert daily_job.should_wait_for_collection(datetime.time(13, 50))


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"PASS: {name}")
