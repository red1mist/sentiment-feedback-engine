"""Rule-based auto-tagging: one `category` for grouping, zero or more triage
`tags`. Deterministic whole-word keyword matching, cheap enough to run on the
write path so a record is stored with everything it needs in one insert."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Category = Literal["billing", "shipping", "support", "product", "performance", "usability", "other"]
CATEGORIES: tuple[str, ...] = ("billing", "shipping", "support", "product", "performance", "usability", "other")

_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "billing": ["bill", "billing", "billed", "charge", "charged", "invoice", "payment", "refund", "price", "pricing", "subscription", "card"],
    "shipping": ["ship", "shipping", "shipped", "delivery", "delivered", "deliver", "package", "parcel", "tracking", "courier", "arrived"],
    "support": ["support", "agent", "representative", "ticket", "helpdesk", "chat", "call", "email", "response", "replied", "reply"],
    "performance": ["slow", "lag", "laggy", "crash", "crashes", "crashed", "freeze", "froze", "timeout", "loading", "performance", "speed", "fast"],
    "usability": ["confusing", "intuitive", "ui", "ux", "design", "layout", "navigate", "navigation", "button", "menu", "hard to find", "easy to use"],
    "product": ["product", "feature", "quality", "works", "broken", "defective", "item", "app", "update", "version"],
}

_TAG_PATTERNS: dict[str, re.Pattern[str]] = {
    "urgent": re.compile(r"\b(urgent|urgently|asap|immediately|right now|emergency)\b"),
    "refund": re.compile(r"\b(refund|money back|chargeback|reimburse)\b"),
    "bug": re.compile(r"\b(bug|crash|crashes|crashed|error|broken|glitch|not working|doesn'?t work)\b"),
    "feature-request": re.compile(r"\b(feature request|would be (great|nice)|wish|please add|add support|suggestion)\b"),
    "churn": re.compile(r"\b(cancel|cancelling|canceling|unsubscribe|switch(ing)? to|never again|leaving)\b"),
}

_CATEGORY_RES = {
    cat: re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b")
    for cat, words in _CATEGORY_KEYWORDS.items()
}


@dataclass(frozen=True)
class Tagging:
    category: str
    tags: list[str]


def tag(text: str) -> Tagging:
    t = text.lower()
    best, best_hits = "other", 0
    for cat in CATEGORIES:
        pattern = _CATEGORY_RES.get(cat)
        if pattern is None:
            continue
        hits = len(pattern.findall(t))
        if hits > best_hits:
            best, best_hits = cat, hits
    tags = [name for name, pattern in _TAG_PATTERNS.items() if pattern.search(t)]
    return Tagging(category=best, tags=tags)
