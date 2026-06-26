"""Text normalization helpers for the filter pipeline.

A single ``normalize_text`` utility is exposed so both the scorer and any
future filter (regex-based, language-specific, etc.) share one definition
of "what a comparable job text looks like".

The rules are deliberately minimal:

* ``None`` becomes an empty string so callers can pass optional ``Job``
  fields (``location``, ``description``, ...) without branching.
* The text is lower-cased so substring matching is case-insensitive.
* Whitespace runs are collapsed to a single space so a keyword like
  ``"new graduate"`` matches regardless of how the source formatted the
  original line breaks or stray spaces.

Turkish characters (``ş``, ``ç``, ``ğ``, ``ı``, ``ö``, ``ü``) are kept as
``str.lower()`` Unicode-aware: no transliteration is applied, so
``"Yazılım"`` and ``"yazılım"`` compare equal but ``"developer"`` will
not match ``"yazılımcı"``. This is the V1 contract; transliteration can
be added later without changing callers.
"""
from __future__ import annotations


def normalize_text(text: str | None) -> str:
    """Return ``text`` in a canonical form for keyword matching.

    Args:
        text: Arbitrary user-supplied string, or ``None``.

    Returns:
        ``text`` lower-cased with internal whitespace runs collapsed to
        a single space and edges stripped. ``None`` yields ``""``.
    """
    if text is None:
        return ""
    return " ".join(text.lower().split())


__all__ = ["normalize_text"]
