"""Lexical retrieval over the recipe-card corpus.

KOTH doctrine applied to RAG: for a curated corpus of ~40 focused cards,
deterministic keyword scoring beats an embedding stack — zero deps, zero
index build, fully inspectable ranking. Upgrade to embeddings only if the
corpus grows past what tags can discriminate.
"""
from __future__ import annotations

import json
import os
import re

_CORPUS_PATH = os.path.join(os.path.dirname(__file__), "corpus.json")
_WORD = re.compile(r"[a-z0-9]{3,}")

_cards: list[dict] | None = None


def load_corpus() -> list[dict]:
    global _cards
    if _cards is None:
        with open(_CORPUS_PATH, encoding="utf-8") as f:
            _cards = list(json.load(f)["cards"])
    return _cards or []


def _terms(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def retrieve(query: str, k: int = 6) -> list[dict]:
    """Top-k cards for a plugin request. Tags weigh most (they are bilingual
    and hand-picked), then title, then notes."""
    q = _terms(query)
    scored = []
    for card in load_corpus():
        tag_terms = _terms(" ".join(card["tags"]))
        title_terms = _terms(card["title"])
        note_terms = _terms(card["notes"])
        score = (3.0 * len(q & tag_terms)
                 + 2.0 * len(q & title_terms)
                 + 0.5 * len(q & note_terms))
        if card["category"].lower() in q:
            score += 2.0
        if score > 0:
            scored.append((score, card))
    scored.sort(key=lambda s: -s[0])
    picked = [c for _, c in scored[:k]]
    # always ground the model in the project skeleton + plugin.yml cards
    for anchor in ("skeleton", "plugin-yml"):
        if not any(anchor in c["id"] for c in picked):
            extra = next((c for c in load_corpus() if anchor in c["id"]), None)
            if extra:
                picked.append(extra)
    return picked


def format_cards(cards: list[dict]) -> str:
    out = []
    for c in cards:
        out.append(f"### {c['title']}  [{c['id']}]\n"
                   f"Notes: {c['notes']}\n"
                   f"```\n{c['code']}\n```")
    return "\n\n".join(out)
