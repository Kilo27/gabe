"""The FAQ: documents a company uploads, used by the Slack FAQ bot (gabe/faq/) and, through
`search_faq`, by Gabe and the team anywhere else a customer question comes up."""

from __future__ import annotations

from gabe.faq.library import FaqLibrary
from gabe.faq.search import load_retriever
from gabe.faq.settings import FaqSettings
from gabe.tools.registry import Risk, ToolRegistry, schema


def setup(registry: ToolRegistry, ctx) -> None:
    settings = FaqSettings.from_dict(ctx.settings, ctx.config.root)

    @registry.add(
        "search_faq",
        "Search the company's FAQ (the documents uploaded for customer questions). Returns the best-matching "
        "questions and answers. Use it before answering a customer, and quote answers rather than guessing.",
        schema(
            {
                "query": {"type": "string", "description": "The customer's question, or key words from it."},
                "limit": {"type": "integer", "description": "How many entries to return (default 5)."},
            },
            required=["query"],
        ),
        Risk.READ,
        "faq",
    )
    def search_faq(query: str, limit: int = 5):
        entries = FaqLibrary(settings.folder).entries()
        if not entries:
            return "The FAQ is empty: no documents have been uploaded."
        hits = load_retriever(settings.retriever).search(query, entries, max(1, min(limit, 20)))
        if not hits:
            return "Nothing in the FAQ matches that."
        return [{"question": e.question, "answer": e.answer, "section": e.section, "source": e.source}
                for e, _ in hits]
