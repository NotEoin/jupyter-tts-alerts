"""End-to-end against a real IPython shell."""

import pytest

from helpers import FakeSpeaker
from jupyter_tts_alerts import api
from jupyter_tts_alerts.config import configure

def callbacks(shell, event):
    return list(shell.events.callbacks[event])


def test_enable_registers_exactly_one_pair_of_handlers(shell):
    api.enable(shell, banner=False)
    before = (len(callbacks(shell, "pre_run_cell")), len(callbacks(shell, "post_run_cell")))
    api.enable(shell, banner=False)
    after = (len(callbacks(shell, "pre_run_cell")), len(callbacks(shell, "post_run_cell")))
    assert before == after
    api.disable(shell)


def test_disable_removes_them_again(shell):
    baseline = len(callbacks(shell, "post_run_cell"))
    api.enable(shell, banner=False)
    assert len(callbacks(shell, "post_run_cell")) == baseline + 1
    api.disable(shell)
    assert len(callbacks(shell, "post_run_cell")) == baseline
    assert api.get_announcer(shell) is None


def test_disable_is_safe_when_nothing_is_attached(shell):
    api.disable(shell)
    api.disable(shell)
    assert api.is_enabled(shell) is False


def test_is_enabled_tracks_both_hooks_and_the_master_switch(hooked):
    shell, _ = hooked
    assert api.is_enabled(shell) is True
    configure(enabled=False)
    assert api.is_enabled(shell) is False


def test_a_real_slow_cell_is_announced(hooked):
    shell, announcer = hooked
    configure(min_seconds=0.2)
    shell.run_cell("import time; time.sleep(0.3)", store_history=True)
    assert any(line.startswith("Cell ") for line in announcer.speaker.lines)


def test_a_real_fast_cell_is_not(hooked):
    shell, announcer = hooked
    configure(min_seconds=30, announce_start=False)
    shell.run_cell("1 + 1", store_history=True)
    assert announcer.speaker.said == []


def test_a_real_failure_is_announced_once_in_the_alert_voice(hooked):
    shell, announcer = hooked
    configure(announce_start=False)
    shell.run_cell("raise ValueError('boom')", store_history=True)
    assert len(announcer.speaker.alerts) == 1
    assert "Value error" in announcer.speaker.alerts[0]


def test_the_announced_number_matches_the_prompt(hooked):
    shell, announcer = hooked
    configure(announce_start=False, min_seconds=0)
    result = shell.run_cell("2 + 2", store_history=True)
    assert f"Cell {result.execution_count}." in announcer.speaker.lines[-1]


def test_a_quiet_cell_stays_quiet_through_the_real_shell(hooked):
    shell, announcer = hooked
    configure(announce_start=False, min_seconds=0)
    shell.run_cell("# tts: off\n3 + 3", store_history=True)
    assert announcer.speaker.said == []


def test_the_magic_is_registered_and_reports_status(hooked):
    shell, _ = hooked
    assert "tts" in shell.magics_manager.magics["line"]
    assert shell.run_line_magic("tts", "") is not None


def test_the_magic_can_change_a_setting(hooked):
    shell, _ = hooked
    shell.run_line_magic("tts", "set min_seconds=42")
    assert api.get_config().min_seconds == 42.0


def test_the_magic_reports_a_bad_setting_without_raising(hooked, capsys):
    shell, _ = hooked
    shell.run_line_magic("tts", "set volume=9000")
    assert "volume must be" in capsys.readouterr().out


def test_the_magic_can_switch_announcements_off(hooked):
    shell, _ = hooked
    shell.run_line_magic("tts", "off")
    assert api.is_enabled(shell) is False


def test_extension_loading_is_the_same_as_enabling(shell):
    import jupyter_tts_alerts

    jupyter_tts_alerts.load_ipython_extension(shell)
    assert api.is_enabled(shell) is True
    jupyter_tts_alerts.unload_ipython_extension(shell)
    assert api.is_enabled(shell) is False


def test_enable_outside_ipython_says_so(monkeypatch):
    monkeypatch.setattr(api, "_find_shell", lambda shell=None: None)
    with pytest.raises(RuntimeError, match="running IPython kernel"):
        api.enable()


def test_enabling_confirms_itself_out_loud(shell):
    """The greeting belongs to the moment you switch it on, not ten minutes later."""
    api.enable(shell, banner=False)
    announcer = api.get_announcer(shell)
    announcer.speaker = FakeSpeaker()
    announcer.pre_run_cell(type("I", (), {"raw_cell": "1+1", "silent": False})())
    assert announcer.speaker.said == []      # nothing at the start of a cell
    api.disable(shell)


def test_the_greeting_can_be_turned_off(shell, monkeypatch):
    from jupyter_tts_alerts import speaker as speaker_module

    spoken = []
    monkeypatch.setattr(
        speaker_module.get_speaker(), "speak",
        lambda text, alert=False: spoken.append(text) or True,
    )
    configure(announce_start=False)
    api.enable(shell, banner=False)
    assert spoken == []
    configure(announce_start=True)
    api.enable(shell, banner=False)
    assert spoken == ["Starting notebook."]
    api.disable(shell)


def test_the_magic_can_select_the_null_engine(hooked):
    """"none" is a real engine name, not a request to auto-detect."""
    shell, _ = hooked
    shell.run_line_magic("tts", "set engine=none")
    assert api.get_config().engine == "none"


def test_the_magic_still_clears_a_voice_with_none(hooked):
    shell, _ = hooked
    configure(voice="en+f2")
    shell.run_line_magic("tts", "set voice=none")
    assert api.get_config().voice is None


def test_say_honours_the_master_switch(pretend_engine, monkeypatch):
    """With the null engine pinned, say() returns False either way."""
    from jupyter_tts_alerts.speaker import get_speaker

    spoken = []
    monkeypatch.setattr(
        get_speaker(), "speak",
        lambda text, alert=False: spoken.append(text) or True,
    )
    assert api.say("this one should be said") is True
    configure(enabled=False)
    assert api.say("this one should not") is False
    assert spoken == ["this one should be said"]


def test_status_explains_silence_rather_than_quoting_a_threshold():
    configure(announce_cells=False)
    assert "no cells (announce_cells is off)" in repr(api.status())
    configure(announce_cells=True, min_error_seconds=30)
    text = repr(api.status())
    assert "cells over 5 seconds" in text
    assert "errors after 30 seconds" in text
    configure(announce_errors=False)
    assert "errors off" in repr(api.status())


def test_status_exposes_every_setting_that_can_cause_silence():
    payload = api.status().as_dict()
    for name in ("announce_cells", "announce_start", "announce_interrupts",
                 "min_error_seconds", "engine_error"):
        assert name in payload


def test_a_missing_configured_engine_is_not_reported_as_no_engine_at_all():
    configure(engine="espeak-ng")
    from jupyter_tts_alerts import engines as engine_module
    from jupyter_tts_alerts.speaker import get_speaker

    original = engine_module.shutil.which
    try:
        engine_module.shutil.which = lambda name: None
        get_speaker()._engine = None
        get_speaker()._engine_key = object()
        report = repr(api.status())
        assert "not installed" in report
        assert "no speech engine found" not in report
    finally:
        engine_module.shutil.which = original


def test_status_is_readable(shell):
    text = repr(api.status(shell))
    assert "jupyter-tts-alerts" in text
    assert "engine" in text
    assert api.status(shell).as_dict()["engine"] == "none"


def test_banner_mentions_the_engine_and_the_threshold(pretend_engine):
    configure(min_seconds=90)
    text = api.banner_text()
    assert "espeak-ng" in text
    assert "1 minute 30 seconds" in text


def test_the_banner_does_not_quote_a_threshold_that_will_never_be_reached(pretend_engine):
    configure(announce_cells=False)
    assert "not announcing cells" in api.banner_text()


def test_a_muted_engine_does_not_look_like_a_working_one():
    configure(engine="none")
    text = api.banner_text()
    assert "nothing will be spoken" in text
    assert "announcing" not in text


def test_arming_twice_automatically_greets_once(shell, monkeypatch):
    """`import jupyter_tts_alerts` then `%load_ext jupyter_tts_alerts`."""
    from jupyter_tts_alerts import speaker as speaker_module

    spoken = []
    monkeypatch.setattr(
        speaker_module.get_speaker(), "speak",
        lambda text, alert=False: spoken.append(text) or True,
    )
    api.disable(shell)
    configure(enabled=True, announce_start=True)
    api.enable(shell, banner=False, activate=False)
    api.enable(shell, banner=False, activate=False)
    assert spoken == ["Starting notebook."]
    api.disable(shell)


def test_say_is_a_no_op_without_a_real_engine():
    assert api.say("hello") is False


def test_voices_is_empty_for_the_null_engine():
    assert api.voices() == []


def test_reset_is_safe_with_nothing_attached(shell):
    api.disable(shell)
    api.reset(shell)


def test_load_ext_does_not_overrule_the_kill_switch(shell, spoken):
    import jupyter_tts_alerts

    api.disable(shell)
    configure(enabled=False, announce_start=True)
    jupyter_tts_alerts.load_ipython_extension(shell)
    assert api.get_config().enabled is False
    assert spoken == []
    api.disable(shell)


@pytest.fixture
def no_engine(monkeypatch):
    """A machine with no speech engine installed at all."""
    from jupyter_tts_alerts import engines
    from jupyter_tts_alerts.speaker import get_speaker

    monkeypatch.setattr(engines.shutil, "which", lambda name: None)
    configure(engine=None)
    monkeypatch.setattr(get_speaker(), "_engine", None)
    monkeypatch.setattr(get_speaker(), "_engine_key", object())


def test_a_machine_with_no_engine_is_told_what_to_install(no_engine):
    from jupyter_tts_alerts.engines import install_hints

    for text in (api.banner_text(), repr(api.status())):
        assert "no speech engine found" in text
        for hint in install_hints():
            assert hint in text


def test_test_without_an_engine_prints_the_hint(no_engine, capsys):
    assert isinstance(api.test(), api.Status)
    assert "no speech engine found" in capsys.readouterr().out


def test_enable_prints_the_banner_when_asked(shell, capsys):
    api.enable(shell, banner=True)
    assert "jupyter-tts-alerts" in capsys.readouterr().out
    api.disable(shell)


def test_load_ext_after_import_keeps_the_notebook_stopwatch(shell):
    """Regression: the second, automatic enable replaced the announcer."""
    api.disable(shell)
    configure(enabled=True)
    first = api.enable(shell, banner=False, activate=False)
    first.notebook_start -= 100
    started = first.notebook_start
    second = api.enable(shell, banner=False, activate=False)
    assert second is first
    assert second.notebook_start == started
    assert api.is_enabled(shell) is True
    api.disable(shell)
