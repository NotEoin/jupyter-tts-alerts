"""The ``%tts`` line magic: the same API, for people who prefer typing less."""

from __future__ import annotations

import shlex
from typing import Any, List

from . import api
from .config import DEFAULT_PHRASES, get_config
from .engines import REGISTRY

USAGE = """\
%tts                      what the package currently thinks it is doing
%tts on | off             start or stop announcing
%tts test                 say a line in each voice
%tts say <text>           say something now
%tts set <key>=<value>    change a setting, e.g. %tts set min_seconds=60
%tts voices               voices this engine accepts
%tts engines              engine names accepted by `set engine=`
%tts reset                restart the whole-notebook stopwatch
%tts help                 this message

Cell directives (in a comment anywhere in the cell, on its own line or after code):
  # tts: off              stay quiet about this cell
  # tts: always           announce it however fast it was
  # tts: final            also announce the total notebook runtime
  # tts: <a sentence>     say that instead of the usual phrasing\
"""


def _run(line: str) -> Any:
    # The command word is split off by hand rather than with shlex, because
    # `%tts say don't stop` is an ordinary thing to type and shlex would
    # reject it for an unbalanced quote.
    head, _, tail = (line or "").strip().partition(" ")
    command = head.lower() if head else "status"
    if command == "say":
        return _say(tail.strip())
    try:
        rest: List[str] = shlex.split(tail)
    except ValueError as exc:  # unbalanced quotes
        print(f"%tts: {exc}")
        return None

    if command in ("status", ""):
        return api.status()
    if command == "on":
        api.enable(banner=False)
        return api.status()
    if command == "off":
        api.disable()
        print("jupyter-tts-alerts: off")
        return None
    if command == "test":
        return api.test()
    if command == "reset":
        api.reset()
        print("jupyter-tts-alerts: notebook stopwatch reset")
        return None
    if command == "voices":
        found = api.voices()
        print("\n".join(found) if found else "this engine does not list voices")
        return None
    if command == "engines":
        print(", ".join(sorted(REGISTRY)))
        return None
    if command == "set":
        return _set(rest)
    if command in ("help", "-h", "--help", "?"):
        print(USAGE)
        return None

    print(f"%tts: unknown command {command!r}\n\n{USAGE}")
    return None


def _say(text: str) -> None:
    if not text:
        print("%tts say <text>")
        return None
    if not api.say(text):
        print("%tts: nothing was said; run %tts to see why")
    return None


def _set(pairs: List[str]) -> Any:
    if not pairs:
        config = get_config()
        for key, value in sorted(vars(config).items()):
            if key != "phrases":
                print(f"{key} = {value!r}")
        for key in sorted(DEFAULT_PHRASES):
            print(f"phrases[{key!r}] = {config.phrase(key)!r}")
        return None
    settings = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep:
            print(f"%tts set: expected key=value, got {pair!r}")
            return None
        key = key.strip()
        # `%tts set voice=none` clears the override -- but "none" is a real
        # engine, the one that makes no sound, so for `engine` the string is
        # what was meant. Guessing wrong there fails towards making noise.
        cleared = value.strip().lower() in ("none", "default") and key != "engine"
        settings[key] = None if cleared else value
    try:
        api.configure(**settings)
    except (TypeError, ValueError) as exc:
        print(f"%tts set: {exc}")
        return None
    return api.status()


def register_magics(shell: Any) -> bool:
    """Attach ``%tts`` to *shell*. Returns False if IPython would not have it."""
    try:
        from IPython.core.magic import Magics, line_magic, magics_class
    except ImportError:  # pragma: no cover - IPython is the host, not a dependency
        return False

    @magics_class
    class TTSMagics(Magics):
        """Speech alerts for long-running cells."""

        # IPython's stubs type line_magic's argument as Never (IPython 9.x).
        @line_magic("tts")  # type: ignore[arg-type]
        def tts(self, line: str) -> Any:
            return _run(line)

        # The same stub makes the method itself Never, so __doc__ is too.
        doc = "Speech alerts for long-running cells.\n\n" + USAGE
        tts.__doc__ = doc  # type: ignore[assignment]
        del doc  # a name in a class body would become a class attribute

    try:
        shell.register_magics(TTSMagics)
    except Exception:  # pragma: no cover - a missing magic is not fatal
        return False
    return True
