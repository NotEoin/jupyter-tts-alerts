"""The decision logic: what gets said, when, and in which voice."""

import time

import pytest

from helpers import FakeInfo, FakeResult
from jupyter_tts_alerts.announcer import Announcer
from jupyter_tts_alerts.config import configure


@pytest.fixture
def announcer(speaker):
    return Announcer(speaker=speaker)


def run_cell(announcer, source="work()", elapsed=0.0, **result_kwargs):
    """Drive one cell through both handlers with a controlled runtime."""
    info = FakeInfo(source)
    announcer.pre_run_cell(info)
    _age(announcer, info, elapsed)
    announcer.post_run_cell(FakeResult(info=info, **result_kwargs))


def _age(announcer, info, elapsed):
    """Backdate the recorded start of an in-flight execution."""
    for index, (recorded, _start) in enumerate(announcer._stack):
        if recorded is info:
            announcer._stack[index] = (recorded, time.monotonic() - elapsed)


def test_fast_cells_say_nothing(announcer, speaker):
    run_cell(announcer, elapsed=1.0)
    assert speaker.said == []


def test_slow_cells_are_announced_with_the_prompt_number(announcer, speaker):
    run_cell(announcer, elapsed=380.0, execution_count=12)
    assert "Cell 12. 6 minutes 20 seconds." in speaker.lines


def test_errors_are_announced_immediately_and_in_the_other_voice(announcer, speaker):
    run_cell(announcer, elapsed=0.1, execution_count=4, error=ValueError("boom"))
    assert speaker.alerts == ["Error in cell 4. Value error."]


def test_a_failing_cell_is_announced_exactly_once(announcer, speaker):
    """The original announced every failure twice, under two different numbers."""
    run_cell(announcer, elapsed=0.1, execution_count=4, error=ValueError("boom"))
    assert len(speaker.alerts) == 1


def test_syntax_errors_are_reported_too(announcer, speaker):
    run_cell(
        announcer,
        elapsed=0.0,
        execution_count=9,
        error=SyntaxError("bad"),
        before_exec=True,
    )
    assert speaker.alerts == ["Error in cell 9. Syntax error."]


def test_interrupting_a_cell_yourself_stays_quiet(announcer, speaker):
    run_cell(announcer, elapsed=600.0, error=KeyboardInterrupt())
    assert speaker.alerts == []


def test_interrupts_can_be_announced_if_you_want_them(announcer, speaker):
    configure(announce_interrupts=True)
    run_cell(announcer, elapsed=600.0, execution_count=3, error=KeyboardInterrupt())
    assert speaker.alerts == ["Error in cell 3. Keyboard interrupt."]


def test_min_error_seconds_can_mute_instant_failures(announcer, speaker):
    configure(min_error_seconds=10)
    run_cell(announcer, elapsed=0.2, error=ValueError("typo"))
    assert speaker.alerts == []
    run_cell(announcer, elapsed=60.0, execution_count=5, error=ValueError("real"))
    assert speaker.alerts == ["Error in cell 5. Value error."]


def test_errors_can_be_switched_off(announcer, speaker):
    configure(announce_errors=False)
    run_cell(announcer, elapsed=99.0, error=ValueError("boom"))
    assert speaker.alerts == []


def test_quiet_directive_silences_the_cell(announcer, speaker):
    run_cell(announcer, source="# tts: off\nwork()", elapsed=600.0)
    assert speaker.said == []


def test_quiet_directive_silences_errors_too(announcer, speaker):
    run_cell(announcer, source="# noannounce\nwork()", elapsed=1.0, error=ValueError())
    assert speaker.said == []


def test_always_directive_beats_the_threshold(announcer, speaker):
    run_cell(announcer, source="# tts: always\nwork()", elapsed=0.2, execution_count=2)
    assert "Cell 2. less than a second." in speaker.lines


def test_a_custom_message_replaces_the_usual_phrasing(announcer, speaker):
    run_cell(announcer, source="# tts: say the model is trained\nwork()", elapsed=0.1)
    assert "the model is trained" in speaker.lines
    assert not any(line.startswith("Cell ") for line in speaker.lines)


def test_final_directive_adds_the_whole_notebook_runtime(announcer, speaker):
    announcer.notebook_start = time.monotonic() - 3725
    run_cell(announcer, source="# tts: final\nwork()", elapsed=1.0, execution_count=30)
    assert speaker.lines[-1] == "Notebook complete. Total runtime 1 hour 2 minutes 5 seconds."


def test_the_master_switch_stops_everything(announcer, speaker):
    configure(enabled=False)
    run_cell(announcer, elapsed=600.0, execution_count=3)
    run_cell(announcer, elapsed=600.0, error=ValueError())
    assert speaker.said == []


def test_silent_executions_are_ignored(announcer, speaker):
    """Frontends run hidden code constantly; none of it is a cell you ran."""
    announcer.pre_run_cell(FakeInfo("completions()", silent=True))
    announcer.post_run_cell(FakeResult(raw_cell="completions()", silent=True))
    assert speaker.said == []


def test_a_missing_result_is_survivable(announcer, speaker):
    announcer.pre_run_cell(FakeInfo("work()"))
    announcer.post_run_cell(None)
    assert speaker.said == []


def test_a_missing_result_does_not_silence_every_later_cell(announcer, speaker):
    """IPython hands post_run_cell None whenever run_cell raises outright."""
    announcer.pre_run_cell(FakeInfo("work()"))
    announcer.post_run_cell(None)
    assert announcer._stack == []          # nothing left in flight
    run_cell(announcer, elapsed=600.0, execution_count=4)
    assert "Cell 4. 10 minutes." in speaker.lines


def test_a_replaced_result_does_not_silence_every_later_cell(announcer, speaker):
    """After an exception escapes, IPython builds a brand new ExecutionResult."""
    announcer.pre_run_cell(FakeInfo("work()"))
    announcer.post_run_cell(FakeResult(raw_cell="work()"))   # a different info
    assert announcer._stack == []
    for number in (5, 6, 7):
        run_cell(announcer, elapsed=600.0, execution_count=number)
    assert [line for line in speaker.lines if line.startswith("Cell ")] == [
        "Cell 5. 10 minutes.", "Cell 6. 10 minutes.", "Cell 7. 10 minutes."
    ]


def test_a_failing_captured_cell_is_not_announced_as_a_success(announcer, speaker):
    """`%%capture` swallows the inner error, so the outer cell looks fine."""
    outer = FakeInfo("%%capture\nboom()")
    inner = FakeInfo("boom()")
    announcer.pre_run_cell(outer)
    announcer.pre_run_cell(inner)
    announcer.post_run_cell(FakeResult(info=inner, execution_count=9, error=ValueError()))
    announcer.post_run_cell(FakeResult(info=outer, execution_count=8))   # no error
    assert speaker.alerts == ["Error in cell 8. Value error."]
    assert not any(line.startswith("Cell 8.") for line in speaker.lines)


def test_an_inherited_error_does_not_leak_into_the_next_cell(announcer, speaker):
    outer, inner = FakeInfo("%%capture\nboom()"), FakeInfo("boom()")
    announcer.pre_run_cell(outer)
    announcer.pre_run_cell(inner)
    announcer.post_run_cell(FakeResult(info=inner, execution_count=9, error=ValueError()))
    announcer.post_run_cell(FakeResult(info=outer, execution_count=8))
    speaker.said.clear()
    run_cell(announcer, elapsed=600.0, execution_count=10)
    assert speaker.alerts == []
    assert "Cell 10. 10 minutes." in speaker.lines


def test_an_empty_cell_cannot_report_the_previous_one(announcer, speaker):
    """Empty cells fire post_run_cell without ever firing pre_run_cell."""
    announcer.post_run_cell(FakeResult(raw_cell="", execution_count=None))
    assert speaker.said == []


def test_a_cell_already_running_when_alerts_start_is_not_announced(announcer, speaker):
    """`import jupyter_tts_alerts` runs inside a cell whose pre event is gone."""
    announcer.post_run_cell(FakeResult(raw_cell="import jupyter_tts_alerts"))
    assert speaker.said == []


def test_the_pairing_does_not_wedge_after_a_skipped_cell(announcer, speaker):
    announcer.post_run_cell(FakeResult(raw_cell=""))        # unpaired, ignored
    run_cell(announcer, elapsed=600.0, execution_count=4)   # paired, announced
    assert "Cell 4. 10 minutes." in speaker.lines


def test_a_nested_execution_is_reported_as_the_cell_the_user_ran(announcer, speaker):
    """`%%capture` runs the body through a second, inner run_cell."""
    outer = FakeInfo("%%capture\ntrain()")
    inner = FakeInfo("train()")
    announcer.pre_run_cell(outer)
    _age(announcer, outer, 600.0)
    announcer.pre_run_cell(inner)                                  # nested
    announcer.post_run_cell(FakeResult(info=inner, execution_count=5))
    assert speaker.said == []                    # the inner one says nothing
    announcer.post_run_cell(FakeResult(info=outer, execution_count=4))
    assert speaker.lines == ["Cell 4. 10 minutes."]   # the outer one, correctly


def test_a_nested_failure_is_not_announced_twice(announcer, speaker):
    outer = FakeInfo("%%capture\nboom()")
    inner = FakeInfo("boom()")
    announcer.pre_run_cell(outer)
    announcer.pre_run_cell(inner)
    announcer.post_run_cell(FakeResult(info=inner, execution_count=8, error=ValueError()))
    announcer.post_run_cell(FakeResult(info=outer, execution_count=7, error=ValueError()))
    assert speaker.alerts == ["Error in cell 7. Value error."]


def test_resetting_the_clock_does_not_mute_the_cell_that_did_it(announcer, speaker):
    """`%tts reset` can only run from inside a cell, usually a long one."""
    info = FakeInfo("get_ipython().run_line_magic('tts', 'reset')\ntrain()")
    announcer.pre_run_cell(info)
    _age(announcer, info, 9000.0)
    announcer.reset()                                  # what %tts reset does
    announcer.post_run_cell(FakeResult(info=info, execution_count=6))
    assert speaker.lines == ["Cell 6. 2 hours 30 minutes."]


def test_phrases_can_be_rewritten(announcer, speaker):
    configure(phrases={"cell": "{cell} took {seconds} seconds."})
    run_cell(announcer, elapsed=61.0, execution_count=7)
    assert "7 took 61.0 seconds." in speaker.lines


def test_notifications_accompany_announcements_when_asked(announcer, speaker):
    configure(notify=True)
    run_cell(announcer, elapsed=600.0, execution_count=8)
    assert speaker.notified[-1][0] == "Cell 8 finished"


def test_failure_notifications_are_marked_urgent(announcer, speaker):
    configure(notify=True)
    run_cell(announcer, elapsed=600.0, execution_count=8, error=ValueError("boom"))
    title, body, urgent = speaker.notified[-1]
    assert (title, urgent) == ("Cell 8 failed", True)
    assert "ValueError: boom" in body


def test_announce_cells_off_still_honours_an_explicit_directive(announcer, speaker):
    configure(announce_cells=False)
    run_cell(announcer, elapsed=600.0, execution_count=4)
    assert not any(line.startswith("Cell 4") for line in speaker.lines)
    run_cell(announcer, source="# tts: say done\nwork()", elapsed=0.1)
    assert "done" in speaker.lines


def test_reset_restarts_the_notebook_stopwatch(announcer, speaker):
    announcer.notebook_start = time.monotonic() - 9999
    announcer.reset()
    run_cell(announcer, source="# tts: final\nwork()", elapsed=0.1)
    assert speaker.lines[-1] == "Notebook complete. Total runtime less than a second."
