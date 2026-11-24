"""Minimal Markdown -> HTML for emailed briefings (headings, bullets, numbered
lists, bold, italics, links, rules). Not a general Markdown renderer."""

from __future__ import annotations

import html
import re

STYLE = (
    "font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;font-size:15px;"
    "line-height:1.5;color:#1f2328;max-width:640px"
)


def _inline(text: str) -> str:
    text = html.escape(text, quote=False)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])", r"<em>\1</em>", text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    return text


def to_html(md: str) -> str:
    out: list[str] = []
    list_tag: str | None = None

    def close_list() -> None:
        nonlocal list_tag
        if list_tag:
            out.append(f"</{list_tag}>")
            list_tag = None

    for raw in md.splitlines():
        line = raw.rstrip()
        if m := re.match(r"^(#{1,4})\s+(.*)", line):
            close_list()
            level = min(len(m.group(1)) + 1, 4)  # h1 in email is too loud
            out.append(f'<h{level} style="margin:18px 0 6px">{_inline(m.group(2))}</h{level}>')
        elif m := re.match(r"^\s*[-*]\s+(.*)", line):
            if list_tag != "ul":
                close_list()
                out.append('<ul style="margin:4px 0 10px;padding-left:22px">')
                list_tag = "ul"
            out.append(f"<li>{_inline(m.group(1))}</li>")
        elif m := re.match(r"^\s*\d+[.)]\s+(.*)", line):
            if list_tag != "ol":
                close_list()
                out.append('<ol style="margin:4px 0 10px;padding-left:22px">')
                list_tag = "ol"
            out.append(f"<li>{_inline(m.group(1))}</li>")
        elif re.match(r"^\s*(-{3,}|\*{3,})\s*$", line):
            close_list()
            out.append('<hr style="border:none;border-top:1px solid #d0d7de;margin:16px 0">')
        elif not line.strip():
            close_list()
        else:
            close_list()
            out.append(f'<p style="margin:6px 0">{_inline(line)}</p>')
    close_list()
    return f'<div style="{STYLE}">' + "\n".join(out) + "</div>"
