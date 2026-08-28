from __future__ import annotations

import pytest

from mediaclipmakarr.process_lock import ProcessLock, ProcessLockError


def test_second_process_lock_fails_clearly(tmp_path) -> None:
    first = ProcessLock(tmp_path / "application.lock")
    second = ProcessLock(tmp_path / "application.lock")
    first.acquire()
    try:
        with pytest.raises(ProcessLockError, match="Another MediaClipMakarr process"):
            second.acquire()
    finally:
        first.release()

    second.acquire()
    second.release()
