"""Turn an uploaded FAQ document into question/answer entries.

Companies write FAQs every which way, so several layouts are understood, tried in order:

1. `Q:` / `A:` markers (also `Question:` / `Answer:`, `Q1.` ...)
2. Markdown headings: each heading is a question, the text under it the answer. A heading
   with nothing under it before the next one is a section name (e.g. "# Billing").
3. Question lines: a line ending in "?" followed by its answer.
4. Anything else: each paragraph becomes a searchable snippet with no question.

Also read: CSV/TSV with question and answer columns, JSON (a list of {question, answer}),
and Word (.docx) files, whose headings count as markdown headings.
"""

from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from dataclasses import asdict, dataclass
from xml.etree import ElementTree

SUFFIXES = (".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".docx")


@dataclass
class Entry:
    id: str
    question: str  # "" for a plain snippet of reference text
    answer: str
    section: str = ""
    source: str = ""  # the document it came from

    def to_dict(self) -> dict:
        return asdict(self)


def parse_document(name: str, data: bytes) -> list[Entry]:
    """Entries from one document. `name` is its file name (the suffix picks the reader)."""
    suffix = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ".txt"
    if suffix not in SUFFIXES:
        raise ValueError(f"Can't read {suffix} files. Use one of: {', '.join(SUFFIXES)}.")
    if suffix == ".docx":
        pairs = parse_text(_docx_text(data))
    else:
        text = _decode(data)
        if suffix in (".csv", ".tsv"):
            pairs = _parse_table(text, "\t" if suffix == ".tsv" else ",")
        elif suffix == ".json":
            pairs = _parse_json(text)
        else:
            pairs = parse_text(text)
    stem = re.sub(r"[^a-z0-9]+", "-", name.rsplit(".", 1)[0].lower()).strip("-") or "faq"
    return [Entry(f"{stem}-{i + 1}", q, a, s, name) for i, (q, a, s) in enumerate(pairs) if (q or a)]


# -- plain text and markdown -----------------------------------------------------

_Q = re.compile(r"^\s*(?:\*\*|__)?\s*(?:Q(?:uestion)?\s*\d*\s*[:.)\-]|Q\d+\s)\s*(?:\*\*|__)?\s*(.*)$", re.I)
_A = re.compile(r"^\s*(?:\*\*|__)?\s*A(?:nswer)?\s*\d*\s*[:.)\-]\s*(?:\*\*|__)?\s*(.*)$", re.I)
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_NUMBERING = re.compile(r"^\s*(?:[-*•]\s+|\d+[.)]\s+|Q\d*[.:)]\s*)")
_EMPHASIS = re.compile(r"^(\*\*|__|\*|_)(.+)\1$")

Triple = tuple[str, str, str]  # question, answer, section


def clean_question(line: str) -> str:
    q = _NUMBERING.sub("", line.strip()).strip()
    m = _EMPHASIS.match(q)
    return (m.group(2) if m else q).strip()


def _join(lines: list[str]) -> str:
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def parse_text(text: str) -> list[Triple]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    for parser in (_parse_markers, _parse_headings, _parse_question_lines):
        found = parser(text)
        if found:
            return found
    return _parse_paragraphs(text)


def _parse_markers(text: str) -> list[Triple]:
    out: list[Triple] = []
    section, q, a, mode = "", None, [], None
    for line in text.split("\n"):
        h = _HEADING.match(line)
        qm, am = _Q.match(line), _A.match(line)
        if qm:
            if q is not None:
                out.append((q, _join(a), section))
            q, a, mode = clean_question(qm.group(1)), [], "q"
        elif am and q is not None:
            a, mode = [am.group(1)], "a"
        elif h and not qm:
            if q is not None:
                out.append((q, _join(a), section))
                q, a, mode = None, [], None
            section = clean_question(h.group(2))
        elif mode == "q" and line.strip():
            q = f"{q} {line.strip()}"  # a question running over two lines
        elif mode == "a":
            a.append(line)
    if q is not None:
        out.append((q, _join(a), section))
    return [x for x in out if x[1]]


def _parse_headings(text: str) -> list[Triple]:
    blocks: list[tuple[int, str, list[str]]] = []
    for line in text.split("\n"):
        h = _HEADING.match(line)
        if h:
            blocks.append((len(h.group(1)), clean_question(h.group(2)), []))
        elif blocks:
            blocks[-1][2].append(line)
    if len(blocks) < 2:
        return []
    out: list[Triple] = []
    section = ""
    for level, title, body in blocks:
        answer = _join(body)
        if not answer:
            section = title
            continue
        # Text under a heading that is itself made of question lines: read those instead.
        inner = _parse_question_lines(answer)
        if len(inner) >= 2 and not title.endswith("?"):
            out += [(q, a, title) for q, a, _ in inner]
        else:
            out.append((title, answer, section))
    return out


def _is_question_line(line: str) -> bool:
    q = clean_question(line)
    return q.endswith("?") and len(q) <= 300


def _parse_question_lines(text: str) -> list[Triple]:
    out: list[Triple] = []
    q, a = None, []
    for line in text.split("\n"):
        if _is_question_line(line) and (q is None or any(x.strip() for x in a)):
            if q is not None:
                out.append((q, _join(a), ""))
            q, a = clean_question(line), []
        elif q is not None:
            a.append(line)
    if q is not None:
        out.append((q, _join(a), ""))
    out = [x for x in out if x[1]]
    return out if len(out) >= 2 or (len(out) == 1 and text.strip().startswith(out[0][0][:20])) else []


def _parse_paragraphs(text: str) -> list[Triple]:
    out: list[Triple] = []
    buf = ""
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        buf = f"{buf}\n\n{para}" if buf else para
        if len(buf) >= 200:  # merge short lines into snippets worth searching
            out.append(("", buf, ""))
            buf = ""
    if buf:
        out.append(("", buf, ""))
    return out


# -- other formats -------------------------------------------------------------

def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def _parse_table(text: str, delimiter: str) -> list[Triple]:
    rows = [r for r in csv.reader(io.StringIO(text), delimiter=delimiter) if any(c.strip() for c in r)]
    if not rows:
        return []
    head = [c.strip().lower() for c in rows[0]]

    def col(*names: str) -> int | None:
        return next((i for i, h in enumerate(head) if any(n in h for n in names)), None)

    qi, ai, si = col("question", "q"), col("answer", "a", "response"), col("section", "category", "topic")
    if qi is None or ai is None or qi == ai:
        qi, ai, si, body = 0, 1, None, rows  # no header row
    else:
        body = rows[1:]
    out = []
    for r in body:
        if len(r) > max(qi, ai):
            out.append((clean_question(r[qi]), r[ai].strip(), r[si].strip() if si is not None and si < len(r) else ""))
    return out


def _parse_json(text: str) -> list[Triple]:
    data = json.loads(text)
    if isinstance(data, dict):
        data = data.get("faqs") or data.get("faq") or data.get("questions") or [
            {"question": k, "answer": v} for k, v in data.items() if isinstance(v, str)
        ]
    if not isinstance(data, list):
        raise ValueError("A JSON FAQ must be a list of {question, answer} items.")
    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        low = {str(k).lower(): v for k, v in item.items()}
        q = low.get("question") or low.get("q") or ""
        a = low.get("answer") or low.get("a") or ""
        s = low.get("section") or low.get("category") or ""
        out.append((clean_question(str(q)), str(a).strip(), str(s)))
    return out


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx_text(data: bytes) -> str:
    """Paragraph text of a Word file, with its headings written as markdown headings."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            root = ElementTree.fromstring(z.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError) as e:
        raise ValueError("That doesn't look like a Word (.docx) file.") from e
    lines = []
    for p in root.iter(f"{_W}p"):
        text = "".join(t.text or "" for t in p.iter(f"{_W}t"))
        style = p.find(f"{_W}pPr/{_W}pStyle")
        name = (style.get(f"{_W}val") or "") if style is not None else ""
        m = re.match(r"(?i)heading\s*(\d)", name)
        level = int(m.group(1)) if m else (1 if name.lower() == "title" else 0)
        if level and text.strip():
            lines += ["", "#" * level + " " + text.strip()]
        else:
            lines.append(text)
    return "\n".join(lines)
