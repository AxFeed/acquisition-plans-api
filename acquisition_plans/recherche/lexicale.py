"""Rank stored chunks by keyword overlap with a query (no language model)."""

import re

from ..stockage.database import PlanDatabase

WORD_PATTERN = re.compile(r"[a-zA-Z0-9_#]{2,}")


def lexical_score(query: str, content: str) -> float:
    """Share of the query keywords found in a chunk, between 0 and 1."""
    query_tokens = {token.lower() for token in WORD_PATTERN.findall(query)}
    if not query_tokens:
        return 0.0
    content_lower = content.lower()
    content_tokens = {token.lower() for token in WORD_PATTERN.findall(content)}
    matched = sum(
        1 for token in query_tokens if token in content_tokens or token in content_lower
    )
    return matched / len(query_tokens)


def search_chunks(database: PlanDatabase, query: str, top_k: int = 4) -> list[dict]:
    """Return the ``top_k`` chunks with the best keyword score, best first."""
    scored = []
    for chunk in database.list_chunks():
        score = lexical_score(query, chunk["content"])
        if score <= 0.0:
            continue
        scored.append(
            {
                "chunk_id": chunk["id"],
                "title": chunk["title"],
                "source_type": chunk["source_type"],
                "source_path": chunk["source_path"],
                "sector": chunk["sector"],
                "content": chunk["content"],
                "score": round(score, 4),
            }
        )
    scored.sort(key=lambda item: item["score"], reverse=True)
    return scored[:top_k]
