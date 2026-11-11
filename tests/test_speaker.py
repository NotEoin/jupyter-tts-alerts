"""The worker thread: it must never block the kernel, and never wedge."""

import sys
import time

import pytest

from jupyter_tts_alerts import speaker as speaker_module
from jupyter_tts_alerts.engines import Command, Engine
from jupyter_tts_alerts.notify import Notifier
from jupyter_tts_alerts.speaker import Speaker

APPEND = (
    "import pathlib, sys; "
    "f = pathlib.Path(sys.argv[1]); "
    "f.write_text(f.read_text() + sys.argv[2] + chr(10)) "
)


class RecordingEngine(Engine):
    """Speaks by appending the message to a file, so a test can read it back."""

    name = "recording"

    def __init__(self, log):
        super().__init__(sys.executable)
        self.log = log
        self.log.write_text("")
        self.cancelled = False

    def speak_command(self, text, voice, volume, rate):
        return Command([sys.executable, "-c", APPEND, str(self.log), f"{voice}:{text}"])

    def default_voices(self):
        return "normal", "alert"

    def cancel_command(self):
        self.cancelled = True
        return None


class SleepEngine(Engine):
    name = "sleep"

    def __init__(self, seconds):
        super().__init__(sys.executable)
        self.seconds = seconds

    def speak_command(self, text, voice, volume, rate):
        return Command([sys.executable, "-c", f"import time; time.sleep({self.seconds})"])


class RecordingNotifier(Notifier):
    name = "recording"

    def __init__(self, log):
        super().__init__(sys.executable)
        self.log = log

    def notify_command(self, title, body, urgent):
        return Command([sys.executable, "-c", APPEND, str(self.log), f"notify:{title}"])


@pytest.fixture
def log(tmp_path):
    return tmp_path / "spoken.txt"


def spoken(log):
    return [line for line in log.read_text().splitlines() if line]


def test_speaking_does_not_block_the_caller(log):
    talker = Speaker(engine=SleepEngine(2))
    started = time.monotonic()
    assert talker.speak("something long") is True
    assert time.monotonic() - started < 0.5
    talker.cancel()
    talker.close(timeout=2)


def test_messages_are_spoken_in_order(log):
    talker = Speaker(engine=RecordingEngine(log))
    for i in range(3):
        talker.speak(f"message {i}")
    assert talker.wait(30) is True
    assert spoken(log) == [f"normal:message {i}" for i in range(3)]


def test_alerts_use_the_second_voice(log):
    talker = Speaker(engine=RecordingEngine(log))
    talker.speak("fine")
    talker.speak("broken", alert=True)
    talker.wait(30)
    assert spoken(log) == ["normal:fine", "alert:broken"]


def test_configured_voices_win_over_the_engine_defaults(log):
    from jupyter_tts_alerts.config import configure

    configure(voice="chosen", error_voice="chosen-alert")
    talker = Speaker(engine=RecordingEngine(log))
    talker.speak("a")
    talker.speak("b", alert=True)
    talker.wait(30)
    assert spoken(log) == ["chosen:a", "chosen-alert:b"]


def test_a_wedged_engine_does_not_block_what_follows(log, monkeypatch):
    monkeypatch.setattr(speaker_module, "SPEAK_TIMEOUT", 0.4)
    talker = Speaker(engine=SleepEngine(30))
    talker.speak("this one hangs")
    assert talker.wait(15) is True  # killed by the timeout rather than hanging


def test_empty_messages_are_dropped(log):
    talker = Speaker(engine=RecordingEngine(log))
    assert talker.speak("   ") is False
    assert talker.speak("") is False


def test_nothing_is_queued_without_an_engine():
    talker = Speaker()
    talker._pinned = None
    talker._engine = None
    talker._engine_key = "none"
    from jupyter_tts_alerts.engines import NullEngine

    talker._pinned = NullEngine()
    assert talker.speak("hello") is False


def test_a_full_backlog_skips_ahead_rather_than_growing(monkeypatch, capsys, log):
    """Announcements arriving faster than they can be said must not stack up."""
    monkeypatch.setattr(speaker_module, "MAX_QUEUED", 2)
    talker = Speaker(engine=SleepEngine(5))
    for i in range(8):
        assert talker.speak(f"message {i}") is True   # the newest always lands
    assert talker._queue.qsize() <= 2
    assert "skipping ahead" in capsys.readouterr().err
    talker.cancel()
    talker.close(timeout=2)


def test_when_the_backlog_is_trimmed_it_is_the_oldest_that_goes(monkeypatch, log):
    monkeypatch.setattr(speaker_module, "MAX_QUEUED", 2)
    talker = Speaker(engine=RecordingEngine(log))
    monkeypatch.setattr(talker, "_ensure_thread", lambda: None)  # nothing drains
    for i in range(5):
        talker.speak(f"message {i}")
    queued = [talker._queue.get_nowait()[1].argv[-1] for _ in range(talker._queue.qsize())]
    assert queued == ["normal:message 3", "normal:message 4"]


def test_cancel_empties_the_backlog(log):
    talker = Speaker(engine=SleepEngine(3))
    for i in range(4):
        talker.speak(f"message {i}")
    talker.cancel()
    assert talker.wait(10) is True
    talker.close(timeout=2)


def test_a_missing_binary_is_reported_once(capsys):
    class BrokenEngine(Engine):
        name = "broken"

        def speak_command(self, text, voice, volume, rate):
            return Command(["/definitely/not/a/program"])

    talker = Speaker(engine=BrokenEngine("/definitely/not/a/program"))
    talker.speak("one")
    talker.speak("two")
    talker.wait(10)
    assert capsys.readouterr().err.count("could not run") == 1


def test_notifications_run_on_the_same_queue(log):
    talker = Speaker(engine=RecordingEngine(log))
    talker._notifier = RecordingNotifier(log)
    talker._notifier_probed = True
    talker.speak("first")
    talker.notify("Cell 3 finished", "Took a minute.")
    talker.wait(30)
    assert spoken(log) == ["normal:first", "notify:Cell 3 finished"]


def test_notifications_report_when_there_is_no_notifier(capsys):
    talker = Speaker(engine=None)
    talker._notifier = None
    talker._notifier_probed = True
    assert talker.notify("title", "body") is False
    assert "no desktop notifier" in capsys.readouterr().err


def test_close_is_idempotent(log):
    talker = Speaker(engine=RecordingEngine(log))
    talker.speak("bye")
    talker.close(timeout=10)
    talker.close(timeout=10)
    assert talker.speak("too late") is False


def test_engine_is_reselected_when_the_setting_changes(capsys):
    from jupyter_tts_alerts.config import configure

    talker = Speaker()
    assert talker.engine is not None and talker.engine.name == "none"
    configure(engine="not-a-real-engine")
    assert talker.engine is None            # re-resolved rather than cached
    configure(engine="none")
    assert talker.engine.name == "none"     # and back again
    capsys.readouterr()


def test_unknown_engine_name_is_reported_once(capsys):
    from jupyter_tts_alerts.config import configure

    talker = Speaker()
    configure(engine="not-a-real-engine")
    assert talker.engine is None
    assert talker.speak("hello") is False
    assert "unknown engine" in capsys.readouterr().err


def test_a_cancel_just_after_dequeue_is_not_lost(log):
    """Regression: the epoch was sampled after get(), so this cancel went unseen."""
    talker = Speaker(engine=RecordingEngine(log))
    real_get = talker._queue.get

    def get_then_cancel(*args, **kwargs):
        item = real_get(*args, **kwargs)
        if item is not None:
            talker.cancel()
        return item

    talker._queue.get = get_then_cancel
    talker.speak("should not be spoken")
    assert talker.wait(10) is True
    assert not log.exists() or log.read_text() == ""
    talker.close(timeout=2)
