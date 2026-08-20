"""The desktop notifiers. Nothing here launches a real notification."""

from __future__ import annotations

import base64

import pytest

from jupyter_tts_alerts import notify
from jupyter_tts_alerts.notify import (
    NotifySend,
    OsascriptNotifier,
    ToastNotifier,
    detect_notifier,
)

ALL = [NotifySend, OsascriptNotifier, ToastNotifier]


@pytest.mark.parametrize("notifier_cls", ALL)
def test_every_notifier_builds_a_runnable_command(notifier_cls):
    command = notifier_cls("/usr/bin/fake").notify_command("Cell 4", "Took a minute.", False)
    assert command.argv and command.argv[0] == "/usr/bin/fake"
    assert all(isinstance(part, str) for part in command.argv)


@pytest.mark.parametrize("notifier_cls", ALL)
def test_a_failed_notification_is_never_swallowed(notifier_cls):
    """A toast that never appears, with nothing said about why, is worse than none."""
    command = notifier_cls("x").notify_command("t", "b", False)
    assert command.warn_on_failure is True


@pytest.mark.parametrize("notifier_cls", ALL)
def test_message_text_never_reaches_a_script_body(notifier_cls):
    nasty = """he said "don't" & `rm -rf /` $(whoami)"""
    command = notifier_cls("x").notify_command("Cell 4 failed", nasty, True)
    for chunk in ("-EncodedCommand", "-e"):
        if chunk in command.argv:
            body = command.argv[command.argv.index(chunk) + 1]
            if chunk == "-EncodedCommand":
                body = base64.b64decode(body).decode("utf-16-le")
            assert nasty not in body
    if command.env:
        assert nasty in " ".join(command.env.values())
    else:
        assert nasty in command.argv          # its own argv element, after --
        assert "--" in command.argv


def test_notify_send_marks_a_failure_as_urgent():
    normal = NotifySend("notify-send").notify_command("t", "b", False).argv
    urgent = NotifySend("notify-send").notify_command("t", "b", True).argv
    assert normal[normal.index("-u") + 1] == "normal"
    assert urgent[urgent.index("-u") + 1] == "critical"


def test_osascript_reads_its_text_from_the_environment():
    """Quotes and apostrophes in an exception must not break the script."""
    command = OsascriptNotifier("osascript").notify_command("Cell 4", "it's broken", False)
    assert command.env == {"JTTS_TITLE": "Cell 4", "JTTS_BODY": "it's broken"}
    assert "system attribute" in command.argv[-1]


def test_the_toast_uses_a_registered_application_id():
    """Windows shows nothing at all for an unregistered AppUserModelID."""
    command = ToastNotifier("powershell").notify_command("t", "b", False)
    assert command.env["JTTS_AUMID"].endswith("powershell.exe")
    script = base64.b64decode(
        command.argv[command.argv.index("-EncodedCommand") + 1]
    ).decode("utf-16-le")
    assert "$env:JTTS_AUMID" in script
    assert "catch{}" not in script            # the failure must not be discarded
    assert "$ErrorActionPreference='Stop'" in script


def test_the_toast_does_not_offer_powershell_7():
    """PowerShell 7 cannot load WinRT types, so it would fail silently."""
    assert ToastNotifier.executables == ("powershell",)


@pytest.mark.parametrize(
    "system, expected",
    [("Linux", NotifySend), ("Darwin", OsascriptNotifier), ("Windows", ToastNotifier)],
)
def test_the_right_notifier_is_chosen_per_platform(monkeypatch, system, expected):
    monkeypatch.setattr(notify.platform, "system", lambda: system)
    assert notify.preference_order()[0] is expected


def test_detection_returns_none_when_nothing_is_installed(monkeypatch):
    monkeypatch.setattr(notify.shutil, "which", lambda name: None)
    assert detect_notifier() is None


@pytest.mark.parametrize(
    "system, expected",
    [("Linux", NotifySend), ("Darwin", OsascriptNotifier), ("Windows", ToastNotifier)],
)
def test_the_installed_notifier_is_detected(monkeypatch, system, expected):
    monkeypatch.setattr(notify.platform, "system", lambda: system)
    monkeypatch.setattr(notify.shutil, "which", lambda name: f"/opt/bin/{name}")
    found = detect_notifier()
    assert type(found) is expected
    assert found.path == f"/opt/bin/{expected.executables[0]}"
