"""Detects text that tries to instruct an AI reader. Posting and mail text are data only."""
from __future__ import annotations

import re

_INJECTION_PATTERNS = [
    r"ignore\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior|above|earlier)\s+(?:instructions|prompts?|rules)",
    r"disregard\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior|above|earlier)",
    r"forget\s+(?:all\s+|your\s+)?(?:previous|prior)\s+instructions",
    r"system\s+prompt",
    r"you\s+are\s+now\s+(?:an?\s+|the\s+)?\w+",
    r"(?:note|message)\s+to\s+(?:any\s+|the\s+)?(?:ai|llm|language\s+model|assistant|automated)",
    r"mark\s+(?:this|the)\s+(?:job|candidate|posting|role|application)\s+as\s+(?:an?\s+)?(?:perfect|great|good|strong)",
    r"(?:perfect|ideal)\s+fit\s+for\s+the\s+candidate",
    r"<\|?(?:im_start|im_end|system)\|?>",
]
_COMPILED = [re.compile(p, re.IGNORECASE) for p in _INJECTION_PATTERNS]


def find_injection(text: str) -> list[str]:
    """Return the snippets in text that look like instructions aimed at an AI reader."""
    return [m.group(0) for pattern in _COMPILED for m in pattern.finditer(text)]
