"""Tiny stopword-ratio language detector for Dutch, German, French and English posting text."""
from __future__ import annotations

import re

_STOPWORDS: dict[str, frozenset[str]] = {
    "en": frozenset(
        "the and of to you your with for will are our have be that this as on an or by from at about "
        "who what which their they them can would should including team work role".split()
    ),
    "nl": frozenset(
        "de het een en van voor met je jij wij ons onze ook bij naar zijn wordt worden als aan dat deze "
        "om niet wat wie jouw er kun kunt heb hebt graag dag per".split()
    ),
    "de": frozenset(
        "der die das und ein eine mit für von den dem des zu sie wir sich auf ist werden bei auch nicht "
        "oder als im ihre ihr sind wird".split()
    ),
    "fr": frozenset(
        "le la les des du et un une pour avec nous vous dans est sont qui que sur par au aux ce cette ou "
        "votre notre être avoir plus".split()
    ),
}
LANGUAGE_NAMES = {"en": "English", "nl": "Dutch", "de": "German", "fr": "French"}
_MIN_WORDS = 30
_MIN_RATIO = 0.08


def detect_language(text: str) -> str:
    """Return 'en', 'nl', 'de' or 'fr', or 'unknown' when the text is short or ambiguous."""
    words = re.findall(r"[a-zà-ÿ]+", text.lower())
    if len(words) < _MIN_WORDS:
        return "unknown"
    scores = sorted(
        ((sum(w in stop for w in words) / len(words), lang) for lang, stop in _STOPWORDS.items()),
        reverse=True,
    )
    (best, lang), (second, _) = scores[0], scores[1]
    if best < _MIN_RATIO or best < second * 1.3:
        return "unknown"
    return lang
