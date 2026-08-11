from __future__ import annotations

import re
import unicodedata

from lingua import Language, LanguageDetectorBuilder
from wordfreq import zipf_frequency  # pyright: ignore[reportMissingTypeStubs]

from zd_agent.casework.policies.reply_quality import public_reply_language_blockers


_LANGUAGE_SEGMENT_RE = re.compile(r"(?<=[.!?])\s+|[\r\n]+")
_WORD_JOINERS = frozenset({"'", "’", "-"})
_ENGLISH_TECHNICAL_FRAGMENT_WORDS = frozenset(
    {
        "account",
        "active",
        "ai",
        "api",
        "app",
        "available",
        "basic",
        "billing",
        "blocked",
        "credit",
        "credits",
        "declined",
        "dify",
        "document",
        "error",
        "exhausted",
        "failed",
        "free",
        "idempotency",
        "issue",
        "limit",
        "model",
        "payment",
        "plan",
        "problem",
        "professional",
        "provider",
        "quota",
        "request",
        "requests",
        "resolvable",
        "resolved",
        "restored",
        "retry",
        "service",
        "serverless",
        "subscription",
        "team",
        "token",
        "tokens",
        "unavailable",
        "webapp",
        "workflow",
        "workspace",
    }
)
_AMBIGUOUS_SHORT_ENGLISH_DETECTIONS = frozenset(
    {Language.POLISH, Language.TSWANA, Language.WELSH}
)
_AMBIGUOUS_LANGUAGE_FUNCTION_WORDS: dict[Language, frozenset[str]] = {
    Language.POLISH: frozenset(
        {
            "ale",
            "bez",
            "być",
            "czy",
            "dla",
            "do",
            "i",
            "jak",
            "jest",
            "lub",
            "ma",
            "na",
            "nie",
            "od",
            "po",
            "są",
            "to",
            "w",
            "z",
            "że",
        }
    ),
    Language.TSWANA: frozenset(
        {
            "ba",
            "bo",
            "e",
            "eng",
            "fa",
            "ga",
            "go",
            "ka",
            "ke",
            "le",
            "lo",
            "mo",
            "na",
            "o",
            "re",
            "se",
            "ya",
        }
    ),
    Language.WELSH: frozenset(
        {
            "a",
            "ac",
            "ar",
            "bod",
            "ddim",
            "gan",
            "gyda",
            "i",
            "mae",
            "mewn",
            "na",
            "neu",
            "ni",
            "o",
            "oedd",
            "ond",
            "sy",
            "sydd",
            "wedi",
            "y",
            "yn",
            "yr",
        }
    ),
}


_DETECTOR = LanguageDetectorBuilder.from_all_languages().build()


def _latin_words(text: str) -> list[str]:
    """Tokenize all Unicode Latin-script words without a finite codepoint range."""
    words: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            word = "".join(current).strip("'’-")
            if word:
                words.append(word)
            current.clear()

    for character in text:
        if character.isalpha() and "LATIN" in unicodedata.name(character, ""):
            current.append(character)
        elif character in _WORD_JOINERS and current:
            current.append(character)
        else:
            flush()
    flush()
    return words


def _is_known_english_technical_fragment(words: list[str]) -> bool:
    return (
        bool(words)
        and len(words) <= 6
        and all(
            word.casefold() in _ENGLISH_TECHNICAL_FRAGMENT_WORDS
            or zipf_frequency(word.casefold(), "en") >= 3.0
            for word in words
        )
    )


def _contains_detected_language_function_word(
    language: Language | None,
    words: list[str],
) -> bool:
    if language is None:
        return False
    markers = _AMBIGUOUS_LANGUAGE_FUNCTION_WORDS.get(language, frozenset())
    return bool({word.casefold() for word in words} & markers)


def public_reply_english_blockers(body: str) -> tuple[str, ...]:
    """Require every customer-visible prose segment to be detected as English."""
    if blockers := public_reply_language_blockers(body):
        return blockers
    saw_words = False
    for segment in _LANGUAGE_SEGMENT_RE.split(body):
        words = _latin_words(segment)
        if not words:
            continue
        saw_words = True
        language = _DETECTOR.detect_language_of(segment.strip())
        if language != Language.ENGLISH and (
            language not in _AMBIGUOUS_SHORT_ENGLISH_DETECTIONS
            or _contains_detected_language_function_word(language, words)
            or not _is_known_english_technical_fragment(words)
        ):
            detected = language.name.casefold() if language is not None else "unknown"
            return (f"public reply was not verified as English (detected {detected})",)
    return () if saw_words else ("public reply contains no verifiable English prose",)
