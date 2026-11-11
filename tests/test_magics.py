"""The `%tts` line magic, driven through a real IPython shell."""

from __future__ import annotations

import time

from jupyter_tts_alerts import api
from jupyter_tts_alerts.magics import USAGE


def test_say_keeps_an_apostrophe_that_shlex_would_reject(hooked, spoken):
    shell, _ = hooked
    shell.run_line_magic("tts", "say don't stop")
    assert spoken == [("don't stop", False)]


def test_on_switches_announcements_back_on_after_off(hooked):
    shell, _ = hooked
    shell.run_line_magic("tts", "off")
    shell.run_line_magic("tts", "on")
    assert api.is_enabled(shell) is True


def test_reset_restarts_the_notebook_stopwatch(hooked):
    shell, announcer = hooked
    announcer.notebook_start = time.monotonic() - 1000
    before = announcer.notebook_start
    shell.run_line_magic("tts", "reset")
    assert announcer.notebook_start > before


def test_test_says_one_line_in_each_voice(hooked, spoken):
    shell, _ = hooked
    shell.run_line_magic("tts", "test")
    assert [alert for _, alert in spoken] == [False, True]


def test_an_unknown_command_prints_the_usage(hooked, capsys):
    shell, _ = hooked
    shell.run_line_magic("tts", "bogus")
    out = capsys.readouterr().out
    assert "unknown command 'bogus'" in out
    assert USAGE in out


def test_help_engines_and_voices_print_without_raising(hooked, capsys):
    shell, _ = hooked
    for command in ("help", "engines", "voices"):
        shell.run_line_magic("tts", command)
    out = capsys.readouterr().out
    assert USAGE in out
    assert "espeak-ng" in out


def test_the_magic_is_documented_for_the_question_mark_help(hooked):
    shell, _ = hooked
    assert USAGE in shell.magics_manager.magics["line"]["tts"].__doc__


def test_set_default_restores_any_setting(hooked):
    shell, _ = hooked
    shell.run_line_magic("tts", "set volume=80 min_seconds=60 notify=yes")
    shell.run_line_magic("tts", "set volume=default min_seconds=default notify=default")
    config = api.get_config()
    assert (config.volume, config.min_seconds, config.notify) == (50, 5.0, False)


def test_set_none_on_a_setting_that_cannot_be_empty_says_so(hooked, capsys):
    """Regression: `volume=none` printed a raw int() TypeError."""
    shell, _ = hooked
    shell.run_line_magic("tts", "set volume=none")
    assert "volume has no 'none' value" in capsys.readouterr().out
    assert api.get_config().volume == 50


def test_set_enabled_none_does_not_switch_announcements_on(hooked, capsys):
    shell, _ = hooked
    api.configure(enabled=False)
    shell.run_line_magic("tts", "set enabled=none")
    assert api.get_config().enabled is False


def test_set_engine_auto_or_default_means_detect(hooked):
    """Regression: the unknown-engine error advised `None`, which stored 'None'."""
    shell, _ = hooked
    for word in ("auto", "AUTO", "default"):
        api.configure(engine="none")
        shell.run_line_magic("tts", f"set engine={word}")
        assert api.get_config().engine is None
