"""Append-only audit log of every tool call Gabe (or a delegate) attempts."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class AuditLog:
    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._lock = threading.Lock()
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, **event: Any) -> None:
        if not self.path:
            return
        event = {"ts": datetime.now(timezone.utc).isoformat(), **event}
        with self._lock, self.path.open("a") as f:
            f.write(json.dumps(event, default=str) + "\n")
