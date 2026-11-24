"""Tiny JSON file store shared by the built-in integrations.

Fine for a single-process deployment; swap for a database-backed integration
when a company needs one.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

# One lock per file, shared by every store on that path (the agent's tools and the
# web app can hold separate JsonStore objects for the same file).
_LOCKS: dict[Path, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.RLock:
    key = path.resolve()
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


class JsonStore:
    def __init__(self, path: Path, default: Any) -> None:
        self.path = path
        self.default = default
        self._lock = _lock_for(path)

    def load(self) -> Any:
        with self._lock:
            if not self.path.exists():
                return json.loads(json.dumps(self.default))
            return json.loads(self.path.read_text())

    def save(self, data: Any) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2, default=str))
            tmp.replace(self.path)

    def update(self, fn):  # fn(data) -> result; data is saved afterwards
        with self._lock:
            data = self.load()
            result = fn(data)
            self.save(data)
            return result
