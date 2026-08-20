"""Shared fixtures. Nothing in the test suite is allowed to make a sound."""

from __future__ import annotations

import pytest

from helpers import FakeSpeaker
from jupyter_tts_alerts import api
from jupyter_tts_alerts import config as config_module
from jupyter_tts_alerts import speaker as speaker_module


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch):
    """Every test starts from stock settings, with audio disabled outright."""
    for name in list(dict(__import__("os").environ)):
        if name.startswith("JUPYTER_TTS_"):
            monkeypatch.delenv(name, raising=False)
    config_module.reset_config()
    config_module.configure(engine="none", banner=False)
    speaker_module._WARNED.clear()   # warn-once is process-wide by design
    yield config_module.get_config()
    config_module.reset_config()


@pytest.fixture
def speaker():
    return FakeSpeaker()


@pytest.fixture(scope="session")
def shell():
    """The real IPython shell the end-to-end tests drive."""
    return pytest.importorskip("IPython.testing.globalipapp").start_ipython()


@pytest.fixture
def hooked(shell):
    """A shell with the package attached and its speech intercepted."""
    api.enable(shell, banner=False)
    announcer = api.get_announcer(shell)
    announcer.speaker = FakeSpeaker()
    yield shell, announcer
    api.disable(shell)


class StubEngine:
    """A speech engine that exists as far as anything except audio is concerned."""

    name = "espeak-ng"
    path = "/usr/bin/espeak-ng"

    def default_voices(self):
        return "en+m3", "en+f3"

    def voices(self):
        return ["en", "en-gb"]


@pytest.fixture
def pretend_engine(monkeypatch):
    """Pins a stand-in engine, so code that needs one runs without audio."""
    monkeypatch.setattr(speaker_module.get_speaker(), "_pinned", StubEngine())
    return StubEngine()


@pytest.fixture
def spoken(monkeypatch):
    """Everything the process-wide speaker is asked to say, as ``(text, alert)``."""
    said = []
    monkeypatch.setattr(
        speaker_module.get_speaker(), "speak",
        lambda text, alert=False: said.append((text, alert)) or True,
    )
    return said
