"""Spotting a kernel that is not running next to the person listening."""

from __future__ import annotations

import pytest

from jupyter_tts_alerts import api, diagnostics

#: Present in every environment below, so the sound-device check never
#: depends on whether the machine running the tests has /dev/snd.
LOCAL_AUDIO = {"PULSE_SERVER": "unix:/run/pulse"}


@pytest.mark.parametrize("name, explanation", diagnostics.REMOTE_SIGNALS)
def test_each_remote_signal_is_reported(name, explanation):
    reasons = diagnostics.remote_reasons({**LOCAL_AUDIO, name: "1"})
    assert reasons == [explanation]
    assert diagnostics.looks_remote({**LOCAL_AUDIO, name: "1"}) is True


def test_two_signals_with_one_meaning_give_one_reason():
    environ = {**LOCAL_AUDIO, "SSH_CONNECTION": "a", "SSH_CLIENT": "b"}
    assert diagnostics.remote_reasons(environ) == [
        "the kernel is on the far end of an SSH connection"
    ]


def test_a_local_kernel_with_audio_raises_no_worries():
    assert diagnostics.remote_reasons(LOCAL_AUDIO) == []
    assert diagnostics.looks_remote(LOCAL_AUDIO) is False


def test_a_missing_sound_device_is_a_reason(monkeypatch):
    monkeypatch.setattr(diagnostics, "has_audio_device", lambda environ=None: False)
    assert diagnostics.remote_reasons({}) == ["no sound device is visible to this process"]


@pytest.mark.parametrize(
    "listing, expected",
    [(OSError("no /dev/snd"), False), ([], False), (["controlC0"], True)],
)
def test_linux_audio_is_found_through_dev_snd(monkeypatch, listing, expected):
    def listdir(path):
        if isinstance(listing, Exception):
            raise listing
        return listing

    monkeypatch.setattr(diagnostics.platform, "system", lambda: "Linux")
    monkeypatch.setattr(diagnostics.os, "listdir", listdir)
    assert diagnostics.has_audio_device({}) is expected


def test_a_pulse_server_counts_as_audio(monkeypatch):
    monkeypatch.setattr(diagnostics.platform, "system", lambda: "Linux")
    monkeypatch.setattr(diagnostics.os, "listdir", lambda path: [])
    assert diagnostics.has_audio_device(LOCAL_AUDIO) is True


def test_other_platforms_are_assumed_to_have_audio(monkeypatch):
    monkeypatch.setattr(diagnostics.platform, "system", lambda: "Darwin")
    assert diagnostics.has_audio_device({}) is True


@pytest.fixture
def over_ssh(monkeypatch):
    """A kernel reached over SSH, with every other remote signal cleared."""
    for name, _ in diagnostics.REMOTE_SIGNALS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SSH_CONNECTION", "10.0.0.1 22 10.0.0.2 22")
    monkeypatch.setattr(diagnostics, "has_audio_device", lambda environ=None: True)


def test_the_banner_warns_about_a_remote_kernel(pretend_engine, over_ssh):
    assert "far end of an SSH connection" in api.banner_text()


def test_status_warns_about_a_remote_kernel(over_ssh):
    assert "far end of an SSH connection" in repr(api.status())
