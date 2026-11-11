"""Tests that run the real machinery rather than a pinned null engine.

Every bug this file covers survived a 175-test suite because that suite
short-circuited exactly here: it pinned `engine="none"`, so no `Command` was
ever executed, and it called `apply_environment()` directly, so the import-time
sequence was never exercised.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time

import pytest

from jupyter_tts_alerts.engines import Command, Engine
from jupyter_tts_alerts.speaker import Speaker

RECORD = (
    "import pathlib, sys; "
    "f = pathlib.Path(sys.argv[1]); "
    "f.write_text(f.read_text() + sys.argv[2] + chr(10))"
)


class ArgvEngine(Engine):
    """Speaks by appending its message to a file, taken from argv."""

    name = "argv-recorder"

    def __init__(self, log):
        super().__init__(sys.executable)
        self.log = log
        self.log.write_text("")

    def speak_command(self, text, voice, volume, rate):
        return Command([sys.executable, "-c", RECORD, str(self.log), text])


class StdinEngine(Engine):
    """Speaks by reading its message on stdin, exactly as termux-tts-speak does."""

    name = "stdin-recorder"

    SCRIPT = (
        "import pathlib, sys; "
        "pathlib.Path(sys.argv[1]).write_text(sys.stdin.read())"
    )

    def __init__(self, log):
        super().__init__(sys.executable)
        self.log = log

    def speak_command(self, text, voice, volume, rate):
        return Command([sys.executable, "-c", self.SCRIPT, str(self.log)], stdin=text)


class EnvEngine(Engine):
    """Takes its message from the environment, as SAPI and two notifiers do."""

    name = "env-recorder"

    SCRIPT = (
        "import os, pathlib, sys; "
        "pathlib.Path(sys.argv[1]).write_text("
        "os.environ['JTTS_TEXT'] + chr(10) + os.environ.get('JTTS_PARENT', ''))"
    )

    def __init__(self, log):
        super().__init__(sys.executable)
        self.log = log

    def speak_command(self, text, voice, volume, rate):
        return Command(
            [sys.executable, "-c", self.SCRIPT, str(self.log)], env={"JTTS_TEXT": text}
        )


class SleepEngine(Engine):
    """Takes longer to speak than anyone is prepared to wait."""

    name = "sleep"

    def __init__(self, seconds=30):
        super().__init__(sys.executable)
        self.seconds = seconds

    def speak_command(self, text, voice, volume, rate):
        return Command([sys.executable, "-c", f"import time; time.sleep({self.seconds})"])


def spawned(monkeypatch):
    """Collect every child the speaker starts, so a test can inspect it."""
    started = []
    real = subprocess.Popen

    class Recording(real):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            started.append(self)

    monkeypatch.setattr(subprocess, "Popen", Recording)
    return started


@pytest.fixture
def log(tmp_path):
    return tmp_path / "spoken.txt"


def test_an_argv_engine_really_speaks(log):
    talker = Speaker(engine=ArgvEngine(log))
    talker.speak("Cell 4. 10 minutes.")
    assert talker.wait(30) is True
    assert log.read_text().strip() == "Cell 4. 10 minutes."
    talker.close(timeout=5)


def test_a_stdin_engine_really_speaks(log):
    """The Termux path: a str handed to a pipe that must be in text mode."""
    talker = Speaker(engine=StdinEngine(log))
    talker.speak("Cell 4. 10 minutes.")
    assert talker.wait(30) is True
    assert log.read_text() == "Cell 4. 10 minutes."
    talker.close(timeout=5)


def test_an_env_engine_really_speaks_and_keeps_the_parent_environment(log, monkeypatch):
    """Replacing the environment outright would drop SYSTEMROOT, which PowerShell needs."""
    monkeypatch.setenv("JTTS_PARENT", "inherited")
    talker = Speaker(engine=EnvEngine(log))
    talker.speak("Cell 4. 10 minutes.")
    assert talker.wait(30) is True
    assert log.read_text().splitlines() == ["Cell 4. 10 minutes.", "inherited"]
    talker.close(timeout=5)


def test_a_message_starting_with_a_dash_survives_argv(log):
    talker = Speaker(engine=ArgvEngine(log))
    talker.speak("-100 percent failure")
    talker.wait(30)
    assert log.read_text().strip() == "-100 percent failure"
    talker.close(timeout=5)


def test_a_timed_out_child_is_killed_not_orphaned(monkeypatch):
    import jupyter_tts_alerts.speaker as speaker_module

    children = spawned(monkeypatch)
    monkeypatch.setattr(speaker_module, "SPEAK_TIMEOUT", 0.4)
    talker = Speaker(engine=SleepEngine(30))
    talker.speak("this one hangs")
    assert talker.wait(20) is True
    assert children and children[0].poll() is not None   # dead and collected
    with talker._lock:
        assert talker._process is None
    talker.close(timeout=5)


def test_the_child_is_reaped_even_when_communicate_fails(monkeypatch, capsys):
    """Only TimeoutExpired used to be recovered from; anything else orphaned."""
    children = spawned(monkeypatch)

    def explode(self, *args, **kwargs):
        raise RuntimeError("something nobody predicted")

    monkeypatch.setattr(subprocess.Popen, "communicate", explode, raising=False)
    talker = Speaker(engine=SleepEngine(30))
    talker.speak("this one blows up")
    assert talker.wait(20) is True
    assert children and children[0].poll() is not None
    assert "something nobody predicted" in capsys.readouterr().err
    talker.close(timeout=5)


def test_wait_does_not_leak_a_thread_per_timeout(monkeypatch):
    import threading

    import jupyter_tts_alerts.speaker as speaker_module

    monkeypatch.setattr(speaker_module, "SPEAK_TIMEOUT", 30)
    talker = Speaker(engine=SleepEngine(30))
    talker.speak("this one hangs")
    before = threading.active_count()
    for _ in range(12):
        assert talker.wait(0.01) is False
    assert threading.active_count() <= before
    talker.cancel()
    talker.close(timeout=5)


def test_cancel_reaches_a_pinned_engines_cancel_command(log):
    """Speaker(engine=...) is documented as a supported way to drive one."""
    cancelled = []

    class Cancellable(ArgvEngine):
        def cancel_command(self):
            cancelled.append(True)
            return Command([sys.executable, "-c", "pass"])

    talker = Speaker(engine=Cancellable(log))
    talker.speak("something")
    talker.cancel()
    assert cancelled == [True]
    talker.close(timeout=5)


def test_a_failing_command_is_reported_only_when_it_asked_to_be(capsys):
    """Only commands whose exit status means something report it."""
    failing = [sys.executable, "-c", "raise SystemExit(3)"]

    class Quiet(Engine):
        name = "quiet"

        def speak_command(self, text, voice, volume, rate):
            return Command(failing)

    class Loud(Engine):
        name = "loud"

        def speak_command(self, text, voice, volume, rate):
            return Command(failing, warn_on_failure=True)

    quiet = Speaker(engine=Quiet(sys.executable))
    quiet.speak("no complaint expected")
    quiet.wait(20)
    assert "exited with status" not in capsys.readouterr().err

    loud = Speaker(engine=Loud(sys.executable))
    loud.speak("complaint expected")
    loud.wait(20)
    assert "exited with status 3" in capsys.readouterr().err
    quiet.close(timeout=5)
    loud.close(timeout=5)


def test_an_utterance_cancelled_before_it_starts_is_abandoned(monkeypatch, log):
    """cancel() used to miss the window between dequeue and spawn."""
    children = spawned(monkeypatch)
    talker = Speaker(engine=SleepEngine(30))
    command = talker.engine.speak_command("should not be spoken", None, 50, 0)
    stale = talker._epoch
    talker._epoch += 1                       # what cancel() does
    talker._run(command, epoch=stale)        # the epoch the worker sampled
    assert children and children[0].poll() is not None
    with talker._lock:
        assert talker._process is None
    talker.close(timeout=5)


# -- the import-time sequence, in a real interpreter --

PROBE = textwrap.dedent(
    """
    from IPython.core.interactiveshell import InteractiveShell
    shell = InteractiveShell.instance()          # a kernel exists before import

    import jupyter_tts_alerts as tts

    print("ENABLED", tts.get_config().enabled)
    print("ATTACHED", tts.get_announcer(shell) is not None)
    print("MIN", tts.get_config().min_seconds)
    shell.run_cell("1 + 1", store_history=True)
    tts.wait(20)
    """
)

def run_probe(shim, **environ):
    directory, log = shim
    log.write_text("")
    env = dict(os.environ)
    for name in list(env):
        if name.startswith("JUPYTER_TTS_"):
            env.pop(name)
    # Only the shim: if it cannot run (a noexec /tmp), the real espeak-ng must
    # not be found in its place and speak. sys.executable is absolute anyway.
    env["PATH"] = str(directory)
    env["JTTS_SHIM_LOG"] = str(log)
    env["JUPYTER_TTS_ENGINE"] = "espeak-ng"          # resolves to the shim
    env.update(environ)
    done = subprocess.run(
        [sys.executable, "-c", PROBE], capture_output=True, text=True, env=env, timeout=180
    )
    assert done.returncode == 0, done.stderr
    result = dict(
        line.split(" ", 1) for line in done.stdout.strip().splitlines() if " " in line
    )
    result["SPOKEN"] = [line for line in log.read_text().splitlines() if line.strip()]
    return result


def test_importing_normally_arms_enables_and_greets(shim):
    result = run_probe(shim)
    assert result["ENABLED"] == "True"
    assert result["ATTACHED"] == "True"
    assert any("Starting notebook" in line for line in result["SPOKEN"])


def test_the_documented_kill_switch_actually_silences_the_package(shim):
    """JUPYTER_TTS_ENABLED=0 used to be undone by the import that read it."""
    result = run_probe(shim, JUPYTER_TTS_ENABLED="0")
    assert result["ENABLED"] == "False"
    assert result["SPOKEN"] == []          # observed at the engine, not in-process


def test_the_kill_switch_holds_for_load_ext_too(shim):
    result = run_probe(shim, JUPYTER_TTS_ENABLED="0", JUPYTER_TTS_AUTOSTART="0")
    assert result["SPOKEN"] == []


def test_other_environment_settings_still_apply_at_import(shim):
    assert run_probe(shim, JUPYTER_TTS_MIN_SECONDS="60")["MIN"] == "60.0"


def test_autostart_off_hooks_nothing_up(shim):
    result = run_probe(shim, JUPYTER_TTS_AUTOSTART="0")
    assert result["ATTACHED"] == "False"
    assert result["SPOKEN"] == []


def test_cancelling_a_command_that_reports_failures_reports_nothing(capsys):
    """Regression: cancel() terminating a notification was blamed on the notifier."""

    class Loud(SleepEngine):
        def speak_command(self, text, voice, volume, rate):
            return super().speak_command(text, voice, volume, rate)._replace(
                warn_on_failure=True
            )

    talker = Speaker(engine=Loud(30))
    talker.speak("interrupted on purpose")
    deadline = time.monotonic() + 20
    while talker._process is None and time.monotonic() < deadline:
        time.sleep(0.05)
    talker.cancel()
    assert talker.wait(20) is True
    assert "exited with status" not in capsys.readouterr().err
    talker.close(timeout=5)


def test_a_failing_espeak_is_reported_with_its_own_message(tmp_path, capsys):
    """Regression: `configure(voice="nonsense")` on espeak-ng was simply silent."""
    if sys.platform.startswith("win"):
        pytest.skip("shell shims are not executable on Windows")
    from jupyter_tts_alerts.engines import EspeakEngine

    fake = tmp_path / "espeak-ng"
    fake.write_text(
        "#!/bin/sh\necho 'Error: The specified espeak-ng voice does not exist.' >&2\nexit 1\n"
    )
    fake.chmod(0o755)
    talker = Speaker(engine=EspeakEngine(str(fake)))
    talker.speak("hello")
    assert talker.wait(20) is True
    err = capsys.readouterr().err
    assert "exited with status 1" in err
    assert "voice does not exist" in err
    talker.close(timeout=5)
