from __future__ import annotations

import re


CARD_PATTERNS = (
    re.compile(r"\binterest rate probabilities\b", re.IGNORECASE),
    re.compile(r"\bimplied volatility\b", re.IGNORECASE),
    re.compile(r"\bcorrelation matrix\b", re.IGNORECASE),
    re.compile(r"\bcurrency strength chart\b", re.IGNORECASE),
)


def is_card_post(title: str) -> bool:
    normalized = " ".join(title.split())
    return any(pattern.search(normalized) for pattern in CARD_PATTERNS)
