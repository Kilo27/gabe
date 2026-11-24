"""Tiny JSON file store shared by the built-in integrations.

Fine for a single-process deployment; swap for a database-backed integration
when a company needs one.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any


class JsonStore:
    def __init__(self, path: Path, default: Any) -> None:
        self.path = path
        self.default = default
        self._lock = threading.Lock()

    def load(self) -> Any:
        if not self.path.exists():
            return json.loads(json.dumps(self.default))
        return json.loads(self.path.read_text())

    def save(self, data: Any) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, default=str))
        tmp.replace(self.path)

    def update(self, fn):  # fn(data) -> result; data is saved afterwards
        with self._lock:
            data = self.load()
            result = fn(data)
            self.save(data)
            return result
