"""OS-enforced single-writer lock for every state-mutating bot process."""
from __future__ import annotations

import fcntl
import json
import os
from datetime import datetime, timezone
from pathlib import Path


class ProcessLockError(RuntimeError):
    pass


class ProcessLock:
    def __init__(self, path: Path, mode: str):
        self.path = Path(path)
        self.mode = mode
        self._fh = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = self.path.open("a+")
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fh.seek(0)
            owner = fh.read().strip() or "unknown owner"
            fh.close()
            raise ProcessLockError(
                f"another trend_bot state writer holds {self.path}: {owner}")
        fh.seek(0)
        fh.truncate()
        fh.write(json.dumps({
            "pid": os.getpid(), "mode": self.mode,
            "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }))
        fh.flush()
        os.fsync(fh.fileno())
        self._fh = fh
        return self

    def release(self):
        if self._fh is None:
            return
        try:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            self._fh.close()
            self._fh = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, exc_type, exc, tb):
        self.release()
