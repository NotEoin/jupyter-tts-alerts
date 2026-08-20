"""Turning numbers and exceptions into things worth hearing."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

#: Anything longer is truncated before it reaches the speech engine. Nobody
#: wants a synthesiser reading a 4,000-character exception message at them.
MAX_SPOKEN_CHARS = 300
#: The ``{error}`` field's share, leaving room for the rest of the phrase.
MAX_ERROR_CHARS = 120

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WHITESPACE = re.compile(r"\s+")
_CAMEL = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z']*|[a-z']+|\d+")
_ORPHAN_PUNCTUATION = re.compile(r"\s+([.,;:!?])")


def format_duration(seconds: float) -> str:
    """``3725`` -> ``"1 hour 2 minutes 5 seconds"``.

    Anything under a second is described rather than rounded, so a forced
    announcement on a fast cell does not claim it took "0 seconds".
    """
    if seconds != seconds or seconds in (float("inf"), float("-inf")):  # NaN / inf
        return "an unknown length of time"
    if seconds < 1:
        return "less than a second"
    total = int(max(0.0, seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    parts = []
    if hours:
        parts.append(_plural(hours, "hour"))
    if minutes:
        parts.append(_plural(minutes, "minute"))
    if secs or not parts:
        parts.append(_plural(secs, "second"))
    return " ".join(parts)


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def humanise_exception(exc: Optional[BaseException]) -> str:
    """``ValueError`` -> ``"value error"``, so the synthesiser reads it as words.

    Runs of capitals are kept intact (``HTTPError`` -> ``"HTTP error"``) because
    engines spell those out letter by letter, which is what you want.
    """
    if exc is None:
        return "error"
    name = type(exc).__name__
    words = _CAMEL.findall(name)
    if not words:
        return "error"
    spoken = []
    for word in words:
        spoken.append(word if word.isupper() and len(word) > 1 else word.lower())
    return " ".join(spoken)


def sentence_case(text: str) -> str:
    """Capitalise the first letter only, leaving acronyms as they are."""
    return text[:1].upper() + text[1:]


def sanitise(text: str, limit: int = MAX_SPOKEN_CHARS) -> str:
    """Tidy *text* into something worth handing to a speech engine.

    Control characters go, whitespace collapses, and over-long text is
    truncated: nobody wants a synthesiser reading a four-thousand-character
    exception at them. Quoting and argv safety are handled structurally by
    :mod:`jupyter_tts_alerts.engines`, not here.
    """
    cleaned = _CONTROL_CHARS.sub(" ", str(text))
    cleaned = _WHITESPACE.sub(" ", cleaned)
    # A template whose {cell} came out empty leaves " ." behind; pull the
    # punctuation back onto the word so it is not read as a pause.
    cleaned = _ORPHAN_PUNCTUATION.sub(r"\1", cleaned).strip()
    if len(cleaned) > limit:
        cleaned = cleaned[: limit - 1].rstrip() + "."
    return cleaned


class _Defaulting(dict):
    """Renders unknown template fields as ``{name}`` instead of raising."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def render(template: str, **fields: Any) -> str:
    """Fill a phrase template, tolerating unknown or malformed fields."""
    try:
        return str(template).format_map(_Defaulting(fields))
    except Exception:
        # Never raise from here. This runs inside the post_run_cell callback,
        # where an exception becomes an IPython "Error in callback" traceback
        # printed after every subsequent cell -- with the cell source in it.
        return str(template)


def describe_fields(elapsed: float, cell: Any, error: Optional[BaseException],
                    total: float) -> Dict[str, Any]:
    """The field set every phrase template can draw on."""
    return {
        "cell": "" if cell is None else cell,
        "duration": format_duration(elapsed),
        "seconds": round(elapsed, 1),
        "total": format_duration(total),
        "total_seconds": round(total, 1),
        # The default phrase puts this at the start of a sentence.
        "error_type": sentence_case(humanise_exception(error)),
        "error": "" if error is None else sanitise(str(error), MAX_ERROR_CHARS),
    }
