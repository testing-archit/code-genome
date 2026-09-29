"""Tokenisation shared by the retrieval and commit-intent models.

Source identifiers are split on case and separators (``parseInvoiceCSV`` becomes
``parse invoice csv``) so natural-language queries can match code entities.
"""

import re

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*|\d+|[ऀ-ॿ]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_CONVENTIONAL = re.compile(r"^\s*([a-zA-Z]+)(\([^)]*\))?(!)?:\s*")

STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "but",
        "by",
        "for",
        "from",
        "has",
        "have",
        "how",
        "i",
        "in",
        "into",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "does",
        "do",
        "did",
        "can",
        "js",
        "ts",
        "tsx",
        "jsx",
        "src",
        "lib",
        "index",
    ]
)

# Hindi and Hinglish words that commonly appear in engineering questions, mapped to the
# English vocabulary used by code and commit messages. This lets the retrieval model
# answer Hinglish queries without any model call.
BILINGUAL_LEXICON: dict[str, str] = {
    "kahan": "where",
    "kaha": "where",
    "kidhar": "where",
    "kya": "what",
    "kaun": "which",
    "kaunsi": "which",
    "kaunsa": "which",
    "kyun": "why",
    "kaise": "how",
    "badlav": "change",
    "badla": "changed",
    "badli": "changed",
    "change": "change",
    "hua": "",
    "hai": "",
    "hain": "",
    "ka": "",
    "ki": "",
    "ke": "",
    "mein": "",
    "me": "",
    "se": "",
    "ko": "",
    "wala": "",
    "wali": "",
    "abhi": "recent",
    "haal": "recent",
    "recently": "recent",
    "naya": "new",
    "nayi": "new",
    "purana": "old",
    "galti": "bug",
    "gadbad": "bug",
    "theek": "fix",
    "sudhar": "fix",
    "jaanch": "test",
    "suraksha": "security",
    "bhugtan": "payment",
    "chalan": "invoice",
    "login": "login",
    "fail": "fail",
    "कहाँ": "where",
    "कहां": "where",
    "क्या": "what",
    "कौन": "which",
    "क्यों": "why",
    "कैसे": "how",
    "बदलाव": "change",
    "बदला": "changed",
    "बदली": "changed",
    "हाल": "recent",
    "नया": "new",
    "नई": "new",
    "फ़ाइल": "file",
    "फाइल": "file",
    "फ़ाइलें": "files",
    "फाइलें": "files",
    "मॉड्यूल": "module",
    "बग": "bug",
    "गलती": "bug",
    "ठीक": "fix",
    "सुधार": "fix",
    "परीक्षण": "test",
    "टेस्ट": "test",
    "सुरक्षा": "security",
    "भुगतान": "payment",
    "चालान": "invoice",
    "बिलिंग": "billing",
    "लॉगिन": "login",
    "निर्यात": "export",
    "आयात": "import",
    "जोखिम": "risk",
    "कमिट": "commit",
    "कोड": "code",
    "सर्वर": "server",
    "डेटाबेस": "database",
    "है": "",
    "हैं": "",
    "में": "",
    "का": "",
    "की": "",
    "के": "",
    "से": "",
    "को": "",
    "और": "",
    "यह": "",
    "वह": "",
    "हुआ": "",
    "हुई": "",
    "था": "",
    "थी": "",
    "कर": "",
}


def split_identifier(token: str) -> list[str]:
    parts = _CAMEL.split(token)
    return [part.lower() for part in parts if part]


def tokenize(text: str, *, translate: bool = True, stem: bool = True) -> list[str]:
    tokens: list[str] = []
    for raw in _WORD.findall(text.replace("_", " ").replace("-", " ").replace("/", " ")):
        pieces = [raw] if not raw.isascii() else split_identifier(raw)
        for piece in pieces:
            word = piece.lower()
            if translate and word in BILINGUAL_LEXICON:
                word = BILINGUAL_LEXICON[word]
            if len(word) < 2 or word in STOPWORDS:
                continue
            tokens.append(_stem(word) if stem else word)
    return tokens


def _stem(word: str) -> str:
    """A light suffix stripper that merges ``change``/``changes``/``changed`` and
    ``export``/``exports``/``exporting``. Consistency matters more than linguistic accuracy."""
    if not word.isascii() or len(word) <= 3:
        return word
    for suffix in ("ations", "ation", "ings", "ing", "ies", "ed", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3 and not word.endswith("ss"):
            word = word[: -len(suffix)] + ("y" if suffix == "ies" else "")
            break
    if len(word) >= 4 and word.endswith("e"):
        word = word[:-1]
    return word


def analyzer(text: str) -> list[str]:
    """Scikit-learn compatible analyzer for identifier-aware word features."""
    return tokenize(text)


def conventional_prefix(message: str) -> tuple[str | None, str]:
    """Return the Conventional Commits type (if any) and the message without it."""
    first_line = message.strip().splitlines()[0] if message.strip() else ""
    match = _CONVENTIONAL.match(first_line)
    if not match:
        return None, first_line
    return match.group(1).lower(), first_line[match.end() :]
