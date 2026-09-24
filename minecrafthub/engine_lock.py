"""Stable process lock shared by managed-engine updates and DLL activation."""
# SPDX-License-Identifier: MIT

import fcntl
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def managed_engine_lock(proton_dir):
    """Serialize mutations using a lock outside the replaceable engine tree."""
    root = Path(proton_dir)
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".bol-engine.lock").open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
