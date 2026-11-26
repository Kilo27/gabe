"""Finding the FAQ entries that bear on a question.

The default is keyword search (BM25 with light stemming), which needs nothing installed and
copes with thousands of entries. A company that wants something else (embeddings, their
help-centre search) sets `retriever: "their_package.module:ClassName"`: any class with

    def __init__(self, settings: dict): ...
    def search(self, query: str, entries: list[Entry], limit: int) -> list[tuple[Entry, float]]
"""

from __future__ import annotations

import importlib
import math
import re
import threading
from collections import Counter
from typing import Any, Protocol

from gabe.faq.parse import Entry


class Retriever(Protocol):
    def search(self, query: str, entries: list[Entry], limit: int) -> list[tuple[Entry, float]]: ...


STOPWORDS = set("""
a about above after again all am an and any are as at be because been before being below between both but by
can could did do does doing down during each few for from further had has have having he her here hers him his
how i if in into is it its itself just me more most my no nor not of off on once only or other our ours out over
own same she should so some such than that the their them then there these they this those through to too under
until up very was we were what when where which while who whom why will with would you your yours hi hello hey
thanks thank please pls get got im ive id dont cant wont let lets know want need anyone someone help
""".split())


def stem(word: str) -> str:
    for suffix, keep in (("ies", "y"), ("ing", ""), ("ed", ""), ("es", ""), ("s", "")):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)] + keep
    return word


def terms(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", text.lower().replace("'", ""))
    return [stem(w) for w in words if w not in STOPWORDS and (len(w) > 1 or w.isdigit())]


class KeywordRetriever:
    """BM25 over each entry's question (counted twice), section and answer."""

    k1, b = 1.4, 0.75

    def __init__(self, settings: dict[str, Any] | None = None) -> None:
        self._index_key: tuple | None = None
        self._docs: list[Counter] = []
        self._df: Counter = Counter()
        self._avg = 1.0
        self._lock = threading.Lock()

    def _index(self, entries: list[Entry]) -> None:
        key = tuple(e.id for e in entries), sum(len(e.answer) for e in entries)
        if key == self._index_key:
            return
        self._docs = [Counter(terms(f"{e.question} {e.question} {e.section} {e.answer}")) for e in entries]
        self._df = Counter(t for d in self._docs for t in d)
        self._avg = (sum(sum(d.values()) for d in self._docs) / len(self._docs)) if self._docs else 1.0
        self._index_key = key

    def search(self, query: str, entries: list[Entry], limit: int) -> list[tuple[Entry, float]]:
        q = set(terms(query))
        if not q or not entries:
            return []
        with self._lock:
            self._index(entries)
            docs, df, avg = self._docs, self._df, self._avg
        n = len(entries)
        scored = []
        for e, d in zip(entries, docs):
            length = sum(d.values()) or 1
            s = 0.0
            for t in q:
                f = d.get(t)
                if not f:
                    continue
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                s += idf * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * length / avg))
            if s > 0:
                scored.append((e, round(s, 3)))
        scored.sort(key=lambda x: -x[1])
        return scored[:limit]


_retrievers: dict[str, Retriever] = {}


def load_retriever(spec: str, settings: dict[str, Any] | None = None) -> Retriever:
    """`keyword`, or "module.path:ClassName" for a company's own. Kept for the process's
    life so an index is built once."""
    spec = spec or "keyword"
    if spec not in _retrievers:
        if spec == "keyword":
            _retrievers[spec] = KeywordRetriever(settings)
        else:
            module, _, cls = spec.partition(":")
            if not cls:
                raise ValueError(f"retriever '{spec}' must look like 'package.module:ClassName'")
            _retrievers[spec] = getattr(importlib.import_module(module), cls)(settings or {})
    return _retrievers[spec]
