"""Crash-safe persistence for paid Stage-2 work."""
from __future__ import annotations

import contextlib
import fcntl
import json
import os


def atomic_write_json(path, value):
    """Replace a JSON record atomically, leaving the prior file on failure."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    temporary = f"{path}.tmp.{os.getpid()}"
    try:
        with open(temporary, "w") as fh:
            json.dump(value, fh, indent=1, ensure_ascii=False, default=str)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


@contextlib.contextmanager
def output_lock(path):
    """Prevent two paid resolver processes from updating one record."""
    lock_path = f"{path}.lock"
    os.makedirs(os.path.dirname(os.path.abspath(lock_path)), exist_ok=True)
    lock = open(lock_path, "w")
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(
                f"another Stage-2 process is already writing {path}") from exc
        lock.write(f"pid={os.getpid()}\n")
        lock.flush()
        yield
    finally:
        lock.close()
