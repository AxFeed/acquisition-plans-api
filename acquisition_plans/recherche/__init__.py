"""Keyword search over the stored text chunks."""

from .lexicale import lexical_score, search_chunks

__all__ = ["lexical_score", "search_chunks"]
