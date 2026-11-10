"""Text helpers: search scoring and sentence selection. Output is always verbatim from the source."""
from __future__ import annotations

import math
import re
from collections import Counter

STOPWORDS = frozenset(
    "a an and are as at be but by for from has have i in is it its me my of on or our please that the their them this to "
    "was we were will with you your find prepare make create get give show".split()
)


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9][a-z0-9_-]+", (text or "").lower())


def query_terms(text: str) -> list[str]:
    return [t for t in tokenize(text) if t not in STOPWORDS]


def score_documents(query: str, docs: list[tuple[str, str]]) -> dict[str, float]:
    """BM25 over (id, text) pairs."""
    terms = query_terms(query)
    if not terms or not docs:
        return {}
    tokenized = {doc_id: Counter(tokenize(text)) for doc_id, text in docs}
    avg_len = max(1.0, sum(sum(c.values()) for c in tokenized.values()) / len(tokenized))
    scores: dict[str, float] = {}
    for doc_id, counts in tokenized.items():
        length = sum(counts.values()) or 1
        score = 0.0
        for term in terms:
            tf = counts.get(term, 0)
            if not tf:
                continue
            df = sum(1 for c in tokenized.values() if term in c)
            idf = math.log(1 + (len(tokenized) - df + 0.5) / (df + 0.5))
            score += idf * (tf * 2.2) / (tf + 1.2 * (0.25 + 0.75 * length / avg_len))
        if score > 0:
            scores[doc_id] = score
    return scores


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n{2,}|\n(?=[-*•\d])", text or "")
    cleaned = [re.sub(r"\s+", " ", p).strip(" -*•") for p in parts if len(p.strip()) > 20]
    # Short lines without closing punctuation are headings or titles, not statements.
    return [s for s in cleaned if s[-1] in ".!?:;" or "|" in s or len(s.split()) >= 8]


def top_sentences(text: str, query: str = "", limit: int = 3) -> list[str]:
    candidates = sentences(text)
    terms = set(query_terms(query))
    ranked = sorted(
        enumerate(candidates),
        key=lambda item: (-len(terms & set(tokenize(item[1]))), bool(re.search(r"\d", item[1])) * -1, item[0]),
    )
    chosen = sorted(ranked[:limit], key=lambda item: item[0])
    return [s for _, s in chosen]


def truncate(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
