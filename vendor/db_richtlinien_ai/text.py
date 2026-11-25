from __future__ import annotations

import re
import unicodedata


TOKEN_PATTERN = re.compile(r"[0-9A-Za-zÄÖÜäöüß]+(?:[-_/][0-9A-Za-zÄÖÜäöüß]+)*")


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    return value.casefold()


def tokenize(value: str) -> list[str]:
    normalized = normalize_text(value)
    return TOKEN_PATTERN.findall(normalized)


# Steuerzeichen, die pdftotext faelschlich zwischen Worte setzt (z.B. STX \x02),
# entfernen und Whitespace zusammenfassen. Zeilenumbrueche bleiben erhalten.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_INLINE_SPACE = re.compile(r"[ \t]+")
_MANY_NEWLINES = re.compile(r"\n{3,}")


def clean_extracted_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = _CONTROL_CHARS.sub(" ", value)
    value = _INLINE_SPACE.sub(" ", value)
    value = _MANY_NEWLINES.sub("\n\n", value)
    return value.strip()

