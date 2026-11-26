"""The FAQ library: a folder of documents a company uploads (or drops in, or edits with
Claude Code). Each file is read with gabe.faq.parse and cached until it changes."""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from gabe.faq.parse import SUFFIXES, Entry, parse_document

MAX_BYTES = 5 * 1024 * 1024

_cache: dict[Path, tuple[float, int, list[Entry] | str]] = {}
_cache_lock = threading.Lock()


@dataclass
class Document:
    name: str
    entries: list[Entry]
    size: int
    updated: str
    error: str = ""

    def summary(self) -> dict:
        return {"name": self.name, "entries": len(self.entries), "questions": sum(1 for e in self.entries if e.question),
                "size": self.size, "updated": self.updated, "error": self.error}


def safe_name(name: str) -> str:
    """A plain file name: no folders, only a known suffix."""
    base = Path(name.replace("\\", "/")).name.strip()
    stem, dot, suffix = base.rpartition(".")
    if not dot or f".{suffix.lower()}" not in SUFFIXES:
        raise ValueError(f"Can't read that file type. Use one of: {', '.join(SUFFIXES)}.")
    stem = re.sub(r"[^\w\- ()]+", "", stem).strip() or "faq"
    return f"{stem[:80]}.{suffix.lower()}"


class FaqLibrary:
    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def _files(self) -> list[Path]:
        if not self.folder.is_dir():
            return []
        return sorted(p for p in self.folder.iterdir()
                      if p.is_file() and p.suffix.lower() in SUFFIXES and not p.name.startswith("."))

    def _read(self, path: Path) -> list[Entry] | str:
        st = path.stat()
        with _cache_lock:
            hit = _cache.get(path)
            if hit and hit[0] == st.st_mtime and hit[1] == st.st_size:
                return hit[2]
        try:
            out: list[Entry] | str = parse_document(path.name, path.read_bytes())
        except Exception as e:  # a broken file is reported, never fatal
            out = f"{e}"
        with _cache_lock:
            _cache[path] = (st.st_mtime, st.st_size, out)
        return out

    def documents(self) -> list[Document]:
        docs = []
        for p in self._files():
            got = self._read(p)
            st = p.stat()
            updated = datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(timespec="seconds")
            docs.append(Document(p.name, got if isinstance(got, list) else [], st.st_size, updated,
                                 got if isinstance(got, str) else ""))
        return docs

    def entries(self) -> list[Entry]:
        return [e for d in self.documents() for e in d.entries]

    def document(self, name: str) -> Document | None:
        return next((d for d in self.documents() if d.name == name), None)

    def add(self, name: str, data: bytes) -> Document:
        """Save an uploaded document, replacing one with the same name. Refused if nothing
        in it can be read as an FAQ."""
        name = safe_name(name)
        if len(data) > MAX_BYTES:
            raise ValueError("That file is over 5 MB.")
        entries = parse_document(name, data)
        if not entries:
            raise ValueError("No questions or answers found in that file.")
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder / name
        tmp = path.with_name("." + name + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
        return self.document(name)

    def remove(self, name: str) -> bool:
        path = self.folder / safe_name(name)
        if not path.is_file():
            return False
        path.unlink()
        return True
