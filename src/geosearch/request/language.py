"""The request's language, decided by code from the prompt (Stage 5, D4).

Why code and not the model: the language drives the provider's result language
(Google's `languageCode`) and the summarizer's output language. A 12B model
filling a `language` argument is one more thing it can get wrong; a script check
is exact and free. Hebrew is the only non-English language supported, so any
Hebrew letter makes the request Hebrew — a Hebrew prompt naming an English
brand ("מסעדה ליד Starbucks") is still a Hebrew prompt.
"""

from typing import Literal

Language = Literal["en", "he"]

# The Hebrew Unicode block: letters, points and punctuation.
_HEBREW_FIRST, _HEBREW_LAST = "֐", "׿"


def detect_language(prompt: str) -> Language:
    if any(_HEBREW_FIRST <= ch <= _HEBREW_LAST for ch in prompt):
        return "he"
    return "en"
