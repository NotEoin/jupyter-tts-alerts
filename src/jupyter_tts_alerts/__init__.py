"""Speech alerts for long-running Jupyter cells.

Import it once and forget about it::

    import jupyter_tts_alerts

From then on, cells that take longer than five seconds announce themselves
when they finish, failures announce themselves immediately in a second voice,
and anything faster than five seconds stays quiet so you are not narrated at
while you write code.

Turn the knobs if the defaults are wrong::

    from jupyter_tts_alerts import configure
    configure(min_seconds=60, volume=80, notify=True)

Every setting except ``phrases`` can also come from the environment: ``JUPYTER_TTS_ENABLED=0``
silences the package without touching a notebook, and
``JUPYTER_TTS_AUTOSTART=0`` stops importing it from hooking anything up.
"""

from __future__ import annotations

import os
import warnings
from typing import Any

from .announcer import Announcer, Directives, parse_directives
from .api import (
    Status,
    banner_text,
    disable,
    enable,
    get_announcer,
    is_enabled,
    reset,
    say,
    status,
    test,
    unhook,
    voices,
    wait,
)
from .config import Config, apply_environment, configure, env_flag, get_config, reset_config
from .diagnostics import looks_remote, remote_reasons
from .engines import detect_engine
from .text import format_duration

__version__ = "1.0.0"

__all__ = [
    "Announcer",
    "Config",
    "Directives",
    "Status",
    "__version__",
    "banner_text",
    "configure",
    "detect_engine",
    "disable",
    "enable",
    "format_duration",
    "get_announcer",
    "get_config",
    "is_enabled",
    "load_ipython_extension",
    "looks_remote",
    "parse_directives",
    "remote_reasons",
    "reset",
    "reset_config",
    "say",
    "status",
    "test",
    "unhook",
    "unload_ipython_extension",
    "voices",
    "wait",
]


def load_ipython_extension(ipython: Any) -> None:
    """Support ``%load_ext jupyter_tts_alerts`` as well as a plain import.

    Like the plain import, this arms the hooks without overruling
    ``JUPYTER_TTS_ENABLED=0``. Otherwise the documented way to silence the
    package on a shared machine would work for one of the two ways of loading
    it and not the other. ``enable()`` and ``%tts on`` are how you say you
    want it on regardless.
    """
    enable(ipython, activate=False)


def unload_ipython_extension(ipython: Any) -> None:
    """Support ``%unload_ext jupyter_tts_alerts``.

    Only unhooks: ``%tts off`` or ``disable()`` is how you switch it off.
    """
    unhook(ipython)


def _autostart() -> None:
    """Hook into the running kernel, if there is one and nobody objected."""
    if not env_flag("AUTOSTART", True):
        return
    try:
        from IPython import get_ipython
    except ImportError:  # plain Python without IPython: nothing to hook into
        return
    shell = get_ipython()
    if shell is None:
        # Imported from a plain script or a test runner: nothing to hook.
        return
    try:
        # activate=False: importing arms the hooks, it does not overrule
        # JUPYTER_TTS_ENABLED=0.
        enable(shell, activate=False)
    except Exception as exc:  # pragma: no cover - never break somebody's import
        warnings.warn(
            f"jupyter-tts-alerts could not start: {exc}", RuntimeWarning, stacklevel=2
        )


apply_environment(os.environ)
_autostart()
