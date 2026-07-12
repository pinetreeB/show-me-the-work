from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import sys
from threading import Event, Thread
from typing import Final

PROGRESS_DELAY_SECONDS: Final = 0.5


@contextmanager
def scan_progress(file_count: int, delay_seconds: float = PROGRESS_DELAY_SECONDS) -> Iterator[None]:
    finished = Event()
    notifier = Thread(
        target=_notify_after_delay,
        args=(finished, file_count, delay_seconds),
        daemon=True,
        name="fable-lite-scan-progress",
    )
    notifier.start()
    try:
        yield
    finally:
        finished.set()
        notifier.join()


def _notify_after_delay(finished: Event, file_count: int, delay_seconds: float) -> None:
    if finished.wait(delay_seconds):
        return
    target = f"{file_count:,}개 파일" if file_count else "프로젝트 파일"
    try:
        _ = sys.stderr.write(f"[fable-lite] {target} 상태 검증 중... 안전하게 변경 여부를 확인하고 있습니다.\n")
        _ = sys.stderr.flush()
    except (OSError, ValueError):
        return
