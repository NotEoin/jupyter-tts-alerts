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
