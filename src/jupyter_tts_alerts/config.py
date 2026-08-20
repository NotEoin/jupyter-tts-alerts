"""Settings for jupyter-tts-alerts.

A single module-level :class:`Config` holds every knob. Defaults are chosen so
that importing the package and never touching it again is the intended way to
use it; :func:`configure` is for when it isn't quite right.

Every field except ``phrases`` can also be set from the environment with a ``JUPYTER_TTS_``
prefix (``JUPYTER_TTS_MIN_SECONDS=60``), which is how you silence the package
on a shared machine or in CI without editing anybody's notebook.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field, fields
from typing import Any, Dict, Mapping, Optional

ENV_PREFIX = "JUPYTER_TTS_"

#: Templates for everything the package says. Fields available to each are
#: listed in the README; unknown fields render as ``{name}`` rather than
#: raising, so a typo costs you one odd-sounding announcement, not a crash.
DEFAULT_PHRASES: Dict[str, str] = {
    "start": "Starting notebook.",
    "cell": "Cell {cell}. {duration}.",
    "error": "Error in cell {cell}. {error_type}.",
    "complete": "Notebook complete. Total runtime {total}.",
}

_TRUTHY = {"1", "true", "yes", "on", "y"}
_FALSEY = {"0", "false", "no", "off", "n", ""}


@dataclass
class Config:
    """Everything jupyter-tts-alerts will let you change."""

    # -- what gets said --
    enabled: bool = True
    min_seconds: float = 5.0
    min_error_seconds: float = 0.0
    announce_start: bool = True
    announce_cells: bool = True
    announce_errors: bool = True
    announce_interrupts: bool = False

    # -- how it sounds --
    engine: Optional[str] = None
    voice: Optional[str] = None
    error_voice: Optional[str] = None
    volume: int = 50
    rate: int = 0

    # -- other channels --
    notify: bool = False

    # -- housekeeping --
    banner: bool = True
    phrases: Dict[str, str] = field(default_factory=lambda: dict(DEFAULT_PHRASES))

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Raise `ValueError` or `TypeError` if any field is out of range."""
        for name in ("min_seconds", "min_error_seconds"):
            value = getattr(self, name)
            # NaN fails every comparison, so a bare `>= 0` check waves it
            # through -- and then no cell ever clears the threshold again.
            if not math.isfinite(value):
                raise ValueError(f"{name} must be a real number, got {value!r}")
        if not 0 <= self.volume <= 100:
            raise ValueError(f"volume must be 0-100, got {self.volume!r}")
        if not -100 <= self.rate <= 100:
            raise ValueError(f"rate must be -100-100, got {self.rate!r}")
        if self.min_seconds < 0:
            raise ValueError(f"min_seconds must be >= 0, got {self.min_seconds!r}")
        if self.min_error_seconds < 0:
            raise ValueError(
                f"min_error_seconds must be >= 0, got {self.min_error_seconds!r}"
            )
        if not isinstance(self.phrases, dict):
            raise TypeError("phrases must be a dict of template strings")

    def phrase(self, key: str) -> str:
        """The template for *key*, falling back to the shipped default."""
        return self.phrases.get(key) or DEFAULT_PHRASES.get(key, "")


_config = Config()


def get_config() -> Config:
    """The live config object. Mutating it works; :func:`configure` validates."""
    return _config


def reset_config() -> Config:
    """Throw away every change, including ones read from the environment."""
    global _config
    _config = Config()
    return _config


def configure(**kwargs: Any) -> Config:
    """Update settings, e.g. ``configure(min_seconds=60, notify=True)``.

    Unknown names raise, with a guess at what you meant. ``phrases`` is merged
    into the existing templates rather than replacing them wholesale.

    All or nothing: if any value is rejected, every field is put back as it
    was. Applying half a call would leave the config in a state that fails
    :meth:`Config.validate`, and since every later ``configure`` call validates
    too, one bad number would raise for the rest of the session, quoting a
    setting the caller had long since stopped touching.
    """
    known = {f.name: f for f in fields(Config)}
    snapshot = {f.name: getattr(_config, f.name) for f in fields(Config)}
    try:
        for key, value in kwargs.items():
            if key not in known:
                raise TypeError(f"unknown setting {key!r}{_did_you_mean(key, known)}")
            if key == "phrases":
                _config.phrases = _merged_phrases(value)
                continue
            setattr(_config, key, _coerce(key, value, known[key].type))
        _config.validate()
    except Exception:
        for name, previous in snapshot.items():
            setattr(_config, name, previous)
        raise
    return _config


def _merged_phrases(value: Any) -> Dict[str, str]:
    """Validate a phrases mapping and fold it into the current templates."""
    if not isinstance(value, dict):
        raise TypeError("phrases must be a dict of template strings")
    unknown = set(value) - set(DEFAULT_PHRASES)
    if unknown:
        raise ValueError(
            "unknown phrase key(s): %s (expected any of %s)"
            % (", ".join(sorted(unknown)), ", ".join(sorted(DEFAULT_PHRASES)))
        )
    # A non-string template raises inside str.format_map, which would surface
    # as a traceback from the post_run_cell callback after every subsequent
    # cell rather than here, where the caller can see what they did.
    not_text = sorted(key for key, text in value.items() if not isinstance(text, str))
    if not_text:
        raise TypeError(
            "phrase template(s) must be strings: %s" % ", ".join(not_text)
        )
    merged = dict(_config.phrases)
    merged.update(value)
    return merged


def _did_you_mean(key: str, known: Dict[str, Any]) -> str:
    import difflib

    close = difflib.get_close_matches(key, known, n=1)
    return f" (did you mean {close[0]!r}?)" if close else ""


def _coerce(key: str, value: Any, annotation: Any) -> Any:
    """Nudge a value into the field's type; strings come from the environment."""
    text = str(annotation)
    if "bool" in text:
        return _as_bool(key, value)
    if "float" in text:
        return None if value is None and "Optional" in text else float(value)
    if "int" in text:
        return None if value is None and "Optional" in text else int(value)
    if value is None:
        return None
    return str(value)


def _as_bool(key: str, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    lowered = str(value).strip().lower()
    if lowered in _TRUTHY:
        return True
    if lowered in _FALSEY:
        return False
    raise ValueError(f"{key} must be a boolean, got {value!r}")


def env_flag(name: str, default: bool = True) -> bool:
    """Read a ``JUPYTER_TTS_``-prefixed boolean straight from the environment."""
    raw = os.environ.get(ENV_PREFIX + name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUTHY


def apply_environment(environ: Optional[Mapping[str, str]] = None) -> Config:
    """Apply ``JUPYTER_TTS_*`` overrides. Called once, at import.

    A bad value is a warning rather than an exception: a stray environment
    variable should not stop somebody's notebook from importing.
    """
    environ = os.environ if environ is None else environ
    if environ.get(ENV_PREFIX + "PHRASES"):
        import warnings

        warnings.warn(
            f"{ENV_PREFIX}PHRASES is not read: phrases are the one setting "
            "with no environment variable; use configure(phrases=...)",
            RuntimeWarning,
            stacklevel=2,
        )
    for f in fields(Config):
        if f.name == "phrases":
            continue
        raw = environ.get(ENV_PREFIX + f.name.upper())
        if raw is None:
            continue
        try:
            configure(**{f.name: raw})
        except (TypeError, ValueError) as exc:
            import warnings

            warnings.warn(
                f"ignoring {ENV_PREFIX}{f.name.upper()}={raw!r}: {exc}",
                RuntimeWarning,
                stacklevel=2,
            )
    return _config
