"""The public surface: enable, disable, configure, say, status, test."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import diagnostics
from .announcer import Announcer
from .config import Config, configure, get_config, reset_config
from .speaker import get_speaker, missing_engine_hint
from .text import format_duration

#: Where the live announcer is parked on the shell, so that a reloaded module
#: or a second ``%load_ext`` can find and replace it instead of stacking a
#: second set of handlers on top of the first.
_ATTR = "_jupyter_tts_alerts"


def _find_shell(shell: Any = None) -> Any:
    if shell is not None:
        return shell
    try:
        from IPython import get_ipython
    except ImportError:  # pragma: no cover - plain Python; the tests always have IPython
        return None
    return get_ipython()


def get_announcer(shell: Any = None) -> Optional[Announcer]:
    """The announcer attached to this shell, if there is one."""
    shell = _find_shell(shell)
    return getattr(shell, _ATTR, None) if shell is not None else None


def enable(shell: Any = None, banner: Optional[bool] = None,
           activate: bool = True) -> Optional[Announcer]:
    """Start announcing. Called for you when the package is imported.

    Safe to call twice: the second call replaces the first announcer's hooks
    rather than adding another set.

    *activate* is what separates "the user asked for this" from "the package
    is waking up". Importing hooks in without it, so ``JUPYTER_TTS_ENABLED=0``
    is not undone by the very import it was set to silence; an explicit
    ``enable()`` or ``%tts on`` does turn it on, which is the whole point of
    typing it.
    """
    shell = _find_shell(shell)
    if shell is None:
        raise RuntimeError(
            "jupyter-tts-alerts needs a running IPython kernel; "
            "import it from inside a notebook or IPython session"
        )
    from .magics import register_magics

    existing = getattr(shell, _ATTR, None)
    # Arming automatically when we are already armed is a no-op: `import`
    # followed by `%load_ext` must not greet you twice, nor restart the
    # notebook stopwatch or forget the cell that is running. Asking
    # explicitly always confirms itself, however many times you ask.
    if not activate and existing is not None and existing.attached and get_config().enabled:
        register_magics(shell)
        return existing
    if existing is not None:
        existing.detach()
    announcer = Announcer().attach(shell)
    setattr(shell, _ATTR, announcer)
    register_magics(shell)
    config = get_config()
    if activate:
        config.enabled = True
    # Everything below is a noise the package makes. A silenced package makes
    # none of them, banner included.
    if not config.enabled:
        return announcer
    if config.banner if banner is None else banner:
        print(banner_text())
    # Say something now rather than at the start of the next cell: the point
    # is to confirm the speakers work, and a greeting that arrives ten minutes
    # later out of a clear sky is not a confirmation of anything.
    if config.announce_start:
        get_speaker().speak(config.phrase("start"))
    return announcer


def disable(shell: Any = None) -> None:
    """Stop announcing and unhook from IPython. ``enable()`` puts it back."""
    unhook(shell)
    get_config().enabled = False


def unhook(shell: Any = None) -> None:
    """Detach from IPython and drop queued speech, leaving the on/off switch alone.

    What ``%unload_ext`` does, so that ``%reload_ext`` comes back the way a
    fresh ``%load_ext`` would, rather than hooked in but switched off.
    """
    announcer = get_announcer(shell)
    if announcer is not None:
        announcer.detach()
    shell = _find_shell(shell)
    if shell is not None and getattr(shell, _ATTR, None) is not None:
        delattr(shell, _ATTR)
    get_speaker().cancel()


def is_enabled(shell: Any = None) -> bool:
    """True when hooks are installed *and* announcements are switched on."""
    announcer = get_announcer(shell)
    return bool(announcer and announcer.attached and get_config().enabled)


def say(text: str, alert: bool = False) -> bool:
    """Say something right now, whatever the thresholds say.

    Useful in the middle of a long cell: ``say("epoch 40 done")``. Thresholds
    do not apply; the master switch does, so somebody else's notebook cannot
    talk on a machine where announcements have been turned off.
    """
    if not get_config().enabled:
        return False
    return get_speaker().speak(text, alert=alert)


def reset(shell: Any = None) -> None:
    """Restart the whole-notebook stopwatch."""
    announcer = get_announcer(shell)
    if announcer is not None:
        announcer.reset()


def wait(timeout: Optional[float] = None) -> bool:
    """Block until everything queued has been spoken."""
    return get_speaker().wait(timeout)


def voices() -> List[str]:
    """Voice names this engine accepts in ``configure(voice=...)``."""
    engine = get_speaker().engine
    if engine is None:
        return []
    try:
        return engine.voices()
    except Exception as exc:  # noqa: BLE001 - a broken listing must not raise out of %tts
        get_speaker()._warn(f"could not list voices: {exc}")
        return []


def test() -> "Status":
    """Say a line in each voice so you can check both are audible.

    Like ``say()``, stays silent while the master switch is off.
    """
    if not get_config().enabled:
        print("jupyter-tts-alerts: announcements are off; run %tts on (or enable()) first")
        return status()
    speaker = get_speaker()
    if speaker.engine is None:
        print(speaker.engine_error or missing_engine_hint())
        return status()
    speaker.speak("This is what a finished cell sounds like.")
    speaker.speak("And this is what a failure sounds like.", alert=True)
    if get_config().notify:
        speaker.notify("jupyter-tts-alerts", "Notifications are working.")
    return status()


class Status:
    """A readable snapshot of what the package thinks it is doing."""

    # Declared so that type checkers see the fields `__init__` fills in.
    version: str
    engine: Optional[str]
    engine_path: Optional[str]
    engine_error: Optional[str]
    voice: Optional[str]
    error_voice: Optional[str]
    volume: int
    rate: int
    min_seconds: float
    min_error_seconds: float
    announce_cells: bool
    announce_start: bool
    announce_errors: bool
    announce_interrupts: bool
    notify: bool
    notifier: Optional[str]
    enabled: bool
    attached: bool
    remote_reasons: List[str]

    def __init__(self, data: Dict[str, Any]) -> None:
        self.__dict__.update(data)
        self._data = data

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def as_dict(self) -> Dict[str, Any]:
        """A copy of the snapshot as a plain dict."""
        return dict(self._data)

    def _cells(self) -> str:
        if not self.announce_cells:
            return "no cells (announce_cells is off)"
        return f"cells over {format_duration(self.min_seconds)}"

    def _errors(self) -> str:
        if not self.announce_errors:
            return "errors off"
        if self.min_error_seconds:
            return f"errors after {format_duration(self.min_error_seconds)}"
        return "errors immediately"

    def __repr__(self) -> str:
        lines = [
            f"jupyter-tts-alerts {self.version}",
            f"  engine     {self.engine or 'none found'}"
            + (f"  ({self.engine_path})" if self.engine_path else ""),
            f"  voices     {self.voice or 'engine default'} / "
            f"{self.error_voice or 'engine default'} (errors)",
            f"  volume     {self.volume}   rate {self.rate:+d}",
            f"  announcing {self._cells()}, {self._errors()}",
            f"  notifications {self.notifier or 'unavailable'}"
            f" ({'on' if self.notify else 'off'})",
            f"  hooks      {'attached' if self.attached else 'detached'}"
            f", announcements {'on' if self.enabled else 'off'}",
        ]
        for reason in self.remote_reasons:
            lines.append(f"  warning: {reason}, so you may not hear anything")
        if not self.engine:
            # The configured engine being absent is a different problem from
            # there being no engine at all, and needs a different answer.
            lines.append(f"  warning: {self.engine_error or missing_engine_hint()}")
        return "\n".join(lines)


def status(shell: Any = None) -> Status:
    """What engine, which voices, whether it is switched on, and any worries."""
    from . import __version__

    config = get_config()
    speaker = get_speaker()
    engine = speaker.engine
    announcer = get_announcer(shell)
    return Status(
        {
            "version": __version__,
            "engine": engine.name if engine else None,
            "engine_path": engine.path if engine else None,
            "engine_error": speaker.engine_error,
            "voice": speaker.voices_for(False),
            "error_voice": speaker.voices_for(True),
            "volume": config.volume,
            "rate": config.rate,
            "min_seconds": config.min_seconds,
            "min_error_seconds": config.min_error_seconds,
            "announce_cells": config.announce_cells,
            "announce_start": config.announce_start,
            "announce_errors": config.announce_errors,
            "announce_interrupts": config.announce_interrupts,
            "notify": config.notify,
            "notifier": speaker.notifier.name if speaker.notifier else None,
            "enabled": config.enabled,
            "attached": bool(announcer and announcer.attached),
            "remote_reasons": diagnostics.remote_reasons(),
        }
    )


def banner_text() -> str:
    """The one line printed on import.

    Deliberately does not resolve voices: that costs a subprocess per engine,
    and import time is the one moment where the package must not do any work
    the user did not ask for.
    """
    from . import __version__

    config = get_config()
    speaker = get_speaker()
    engine = speaker.engine
    if engine is None:
        reason = speaker.engine_error or missing_engine_hint()
        return f"jupyter-tts-alerts {__version__}: {reason}"
    if engine.name == "none":
        # Explicitly muted. Saying "announcing cells over five seconds" here
        # would look exactly like a working install that has gone quiet.
        return (
            f"jupyter-tts-alerts {__version__}  |  engine: none  |  "
            "nothing will be spoken"
        )
    line = "  |  ".join([
        f"jupyter-tts-alerts {__version__}",
        f"engine: {engine.name}",
        # What will actually happen, not the threshold alone: with
        # announce_cells off the threshold is not the operative fact.
        f"announcing cells over {format_duration(config.min_seconds)}"
        if config.announce_cells
        else "not announcing cells (announce_cells is off)",
    ])
    for reason in diagnostics.remote_reasons():
        line += f"\n   warning: {reason}, so you may not hear anything"
    return line


__all__ = [
    "Config",
    "Status",
    "banner_text",
    "configure",
    "disable",
    "enable",
    "get_announcer",
    "get_config",
    "is_enabled",
    "reset",
    "reset_config",
    "say",
    "status",
    "test",
    "unhook",
    "voices",
    "wait",
]
