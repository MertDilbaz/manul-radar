"""Text normalization helpers for the filter pipeline.

The scorer compares Turkish and English job text coming from different
sources. Sources may use Turkish characters, ASCII fallbacks,
underscores in source names, or punctuation-heavy titles. This module
normalizes those variants into one searchable form so config keywords
such as ``yazilim destek`` can match both ``Yazılım Destek`` and
``kariyer_net_yazilim_destek``.
"""
from __future__ import annotations

import re

_TURKISH_TRANSLATION = str.maketrans(
    {
        "ç": "c",
        "ğ": "g",
        "ı": "i",
        "ö": "o",
        "ş": "s",
        "ü": "u",
        "Ç": "c",
        "Ğ": "g",
        "İ": "i",
        "I": "i",
        "Ö": "o",
        "Ş": "s",
        "Ü": "u",
    }
)
_NON_WORD_RE = re.compile(r"[^a-z0-9+#.]+")
_SPACE_RE = re.compile(r"\s+")


def normalize_text(text: str | None) -> str:
    """Return ``text`` in a canonical form for keyword matching."""
    if text is None:
        return ""
    normalized = str(text).translate(_TURKISH_TRANSLATION).lower()
    normalized = normalized.replace("_", " ").replace("-", " ").replace("/", " ")
    normalized = _NON_WORD_RE.sub(" ", normalized)
    return _SPACE_RE.sub(" ", normalized).strip()


__all__ = ["normalize_text"]
