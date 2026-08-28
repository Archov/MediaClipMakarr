from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import IO


class ProcessLockError(RuntimeError):
    pass


class ProcessLock:
    """Cross-platform, non-blocking exclusive lock retained for the process lifetime."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._file: IO[bytes] | None = None

    @property
    def acquired(self) -> bool:
        return self._file is not None

    def acquire(self) -> None:
        if self.acquired:
            raise ProcessLockError("The application process lock is already held by this instance.")

        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT)
        lock_file = os.fdopen(descriptor, "r+b", buffering=0)
        try:
            lock_file.seek(0)
            if lock_file.read(1) == b"":
                lock_file.seek(0)
                lock_file.write(b"0")
                lock_file.flush()
            lock_file.seek(0)
            self._lock_file(lock_file)
        except OSError as error:
            lock_file.close()
            raise ProcessLockError(
                "Another MediaClipMakarr process is already using this private-data directory. "
                "Stop that process or configure a different MCM_PRIVATE_DATA_DIR."
            ) from error

        self._file = lock_file
        metadata = json.dumps(
            {"pid": os.getpid(), "started_at": datetime.now(UTC).isoformat()},
            separators=(",", ":"),
        ).encode("utf-8")
        # Byte zero remains stable for the lifetime of the OS-level lock. Metadata
        # starts after it so updating diagnostics cannot invalidate the lock range.
        lock_file.seek(1)
        lock_file.truncate()
        lock_file.write(metadata)
        lock_file.flush()

    def release(self) -> None:
        if self._file is None:
            return
        lock_file = self._file
        self._file = None
        try:
            lock_file.seek(0)
            self._unlock_file(lock_file)
        finally:
            lock_file.close()

    @staticmethod
    def _lock_file(lock_file: IO[bytes]) -> None:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock_file(lock_file: IO[bytes]) -> None:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def __enter__(self) -> ProcessLock:
        self.acquire()
        return self

    def __exit__(self, *_: object) -> None:
        self.release()
