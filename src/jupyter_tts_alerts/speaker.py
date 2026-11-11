"""The bit that actually makes noise.

Announcements go onto a queue and are spoken by one background daemon thread,
which matters more than it sounds: the obvious implementation blocks the
kernel until the previous sentence has finished, so a chatty notebook ends up
waiting on its own speech before it will run the next cell.
"""

from __future__ import annotations

import atexit
import os
import queue
import subprocess
import sys
import threading
from typing import Optional, Set, Tuple

from .config import get_config
from .engines import Command, Engine, detect_engine, install_hints, load_engine
from .notify import Notifier, detect_notifier

#: Give up on a single utterance after this long -- a wedged synthesiser must
#: not be able to block every announcement that follows it.
SPEAK_TIMEOUT = 60.0
#: How many announcements may wait to be spoken. Deliberately small: a
#: notebook of cells that each just clear the threshold can generate them
#: faster than they can be said, and speech that carries on for a minute
#: after the work finished is worse than no speech at all. When the queue
#: is full the *oldest* pending item is dropped, because the newest is
#: always the one worth hearing.
MAX_QUEUED = 4
#: Problems already reported. Process-wide, so a reload does not re-nag.
_WARNED: Set[str] = set()

#: How long to keep the kernel alive at shutdown finishing the last sentence.
SHUTDOWN_GRACE = 3.0
#: Cancel commands are trivial; do not let one hang around for a minute.
CANCEL_TIMEOUT = 5.0


class Speaker:
    """Serialises speech and notifications onto one background thread."""

    def __init__(self, engine: Optional[Engine] = None) -> None:
        #: Set to pin one engine and skip detection entirely -- used by the
        #: tests, and by anyone driving a synthesiser we do not ship support for.
        self._pinned = engine
        #: Jobs as ``(epoch, command)``, stamped when submitted; None stops the worker.
        self._queue: "queue.Queue[Optional[Tuple[int, Command]]]" = queue.Queue(MAX_QUEUED)
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._process: Optional[subprocess.Popen] = None
        self._closed = False
        #: Bumped by cancel(). Every job is stamped with it when submitted, so
        #: a job cancelled at any point before its process starts, including
        #: just after the worker dequeued it, is abandoned.
        self._epoch = 0
        self._engine: Optional[Engine] = None
        self._engine_key: object = object()
        self._engine_error: Optional[str] = None
        self._notifier: Optional[Notifier] = None
        self._notifier_probed = False

    # -- engine resolution --
    @property
    def engine(self) -> Optional[Engine]:
        """The engine in force, re-detected if ``configure(engine=...)`` ran."""
        if self._pinned is not None:
            return self._pinned
        requested = get_config().engine
        if self._engine is None or requested != self._engine_key:
            self._engine_key = requested
            try:
                self._engine = load_engine(requested) if requested else detect_engine()
                self._engine_error = None
            except (ValueError, RuntimeError) as exc:
                # Keep the reason. Reporting "no speech engine found" on a
                # machine that has three of them, because the configured name
                # was wrong, sends people down entirely the wrong path.
                self._engine_error = str(exc)
                self._warn(self._engine_error)
                self._engine = None
        return self._engine

    @property
    def engine_error(self) -> Optional[str]:
        """Why the configured engine could not be used, if that is the case."""
        # The property resolves the engine, so the answer is never stale.
        self.engine  # noqa: B018 - evaluated for its side effect
        return self._engine_error

    @property
    def notifier(self) -> Optional[Notifier]:
        """The desktop notifier, detected once on first use, or `None`."""
        if not self._notifier_probed:
            self._notifier_probed = True
            self._notifier = detect_notifier()
        return self._notifier

    def voices_for(self, alert: bool) -> Optional[str]:
        """The configured voice, or the engine's own suggestion."""
        config = get_config()
        chosen = config.error_voice if alert else config.voice
        if chosen:
            return chosen
        engine = self.engine
        if engine is None:
            return None
        normal, warning = self._default_voices(engine)
        return warning if alert else normal

    def _default_voices(self, engine: Engine) -> Tuple[Optional[str], Optional[str]]:
        cached = getattr(engine, "_cached_default_voices", None)
        if cached is None:
            try:
                cached = engine.default_voices()
            except Exception:  # a voice listing must never break an announcement
                cached = (None, None)
            engine._cached_default_voices = cached
        return cached

    # -- public API --
    def speak(self, text: str, alert: bool = False) -> bool:
        """Queue *text*. Returns False if there is nothing to speak it with."""
        engine = self.engine
        if engine is None or not str(text).strip():
            return False
        config = get_config()
        try:
            command = engine.speak_command(
                text, self.voices_for(alert), config.volume, config.rate
            )
        except Exception as exc:  # noqa: BLE001 - speak() runs inside post_run_cell, where a
            # raise prints a callback traceback after every later cell
            self._warn(f"could not build a speech command: {exc}")
            return False
        if not command.argv:
            return False
        return self._submit(command)

    def notify(self, title: str, body: str, urgent: bool = False) -> bool:
        """Queue a desktop notification behind whatever is already speaking."""
        notifier = self.notifier
        if notifier is None:
            self._warn("no desktop notifier found; notifications are off")
            return False
        return self._submit(notifier.notify_command(title, body, urgent))

    def cancel(self) -> None:
        """Drop everything queued and stop whatever is speaking right now."""
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
            else:
                self._queue.task_done()
        with self._lock:
            self._epoch += 1
            process, self._process = self._process, None
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
        # Not self.engine: that re-resolves through the config, so cancelling
        # after configure(engine=...) would consult the new engine about an
        # utterance the old one is speaking.
        engine = self._engine or self._pinned
        if engine is not None:
            command = engine.cancel_command()
            if command is not None:
                try:
                    subprocess.run(
                        command.argv,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=CANCEL_TIMEOUT,
                    )
                except (OSError, subprocess.SubprocessError):
                    pass

    def wait(self, timeout: Optional[float] = None) -> bool:
        """Block until the backlog is spoken. Returns False if it timed out.

        Waits on the same condition ``Queue.join`` uses, rather than parking a
        thread in ``join()`` and walking away from it: there is no way to
        interrupt ``join``, so a timed-out wait would leave that thread alive
        for as long as the backlog took to drain, or forever if it never did.
        """
        pending = self._queue
        with pending.all_tasks_done:
            return bool(
                pending.all_tasks_done.wait_for(
                    lambda: pending.unfinished_tasks == 0, timeout
                )
            )

    def close(self, timeout: float = SHUTDOWN_GRACE) -> None:
        """Finish the current sentence, then retire the worker thread."""
        if self._closed:
            return
        self._closed = True
        thread = self._thread
        if thread is not None and thread.is_alive():
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                self.cancel()
                try:
                    self._queue.put_nowait(None)
                except queue.Full:
                    pass
            thread.join(timeout)

    # -- plumbing --
    def _submit(self, command: Command) -> bool:
        if self._closed:
            return False
        self._ensure_thread()
        with self._lock:
            stamp = self._epoch
        for _ in range(2):
            try:
                self._queue.put_nowait((stamp, command))
            except queue.Full:
                # Make room by discarding the oldest thing still waiting: if
                # announcements are arriving faster than they can be spoken,
                # the stale ones are the ones nobody needs to hear.
                self._warn("speaking more slowly than cells finish; skipping ahead")
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    pass
                else:
                    self._queue.task_done()
            else:
                return True
        return False

    def _ensure_thread(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._worker, name="jupyter-tts-alerts", daemon=True
            )
            self._thread.start()

    def _worker(self) -> None:
        while True:
            job = self._queue.get()
            try:
                if job is None:
                    return
                epoch, command = job
                self._run(command, epoch=epoch)
            except Exception as exc:  # never let the worker die
                self._warn(f"TTS failed: {exc}")
            finally:
                self._queue.task_done()

    def _run(self, command: Command, timeout: Optional[float] = None,
             epoch: Optional[int] = None) -> None:
        # Read the module constant at call time so it can be adjusted.
        timeout = SPEAK_TIMEOUT if timeout is None else timeout
        env = None
        if command.env:
            env = dict(os.environ)
            env.update(command.env)
        # Engines that take their text on stdin are handed a str, so the pipe
        # has to be in text mode; on a binary pipe communicate() raises
        # TypeError before a byte reaches the synthesiser.
        wants_stdin = command.stdin is not None
        try:
            process = subprocess.Popen(
                command.argv,
                stdin=subprocess.PIPE if wants_stdin else subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                encoding="utf-8" if wants_stdin else None,
                errors="replace" if wants_stdin else None,
            )
        except OSError as exc:
            self._warn(f"could not run {command.argv[0]!r}: {exc}")
            return

        with self._lock:
            stale = epoch is not None and epoch != self._epoch
            if not stale:
                self._process = process
        if stale:
            # Cancelled after it was submitted, before it was started.
            self._reap(process)
            return

        try:
            process.communicate(input=command.stdin, timeout=timeout)
        except subprocess.TimeoutExpired:
            self._warn(f"{command.argv[0]!r} timed out and was stopped")
        finally:
            with self._lock:
                if self._process is process:
                    self._process = None
            # Every exit path, not just the timeout: an exception out of
            # communicate() would otherwise leave the child running, unreaped
            # and -- if it reads stdin -- blocked on a pipe nobody will close.
            killed = self._reap(process)

        # A process we killed ourselves has a nonzero status by definition;
        # reporting that as a failure of the command would be blaming it for
        # something we did, and the timeout has already been reported.
        if command.warn_on_failure and process.returncode and not killed:
            self._warn(
                f"{command.argv[0]!r} exited with status {process.returncode}"
            )

    def _reap(self, process: subprocess.Popen) -> bool:
        """Make sure *process* is dead and collected. True if we killed it."""
        killed = process.poll() is None
        if killed:
            try:
                process.kill()
            except OSError:
                pass
        if process.stdin is not None and not process.stdin.closed:
            try:
                process.stdin.close()
            except OSError:
                pass
        try:
            process.wait(timeout=CANCEL_TIMEOUT)
        except subprocess.TimeoutExpired:  # pragma: no cover - a kill ignored
            pass
        return killed

    def _warn(self, message: str) -> None:
        """Complain once per distinct problem, then stay quiet about it."""
        if message in _WARNED:
            return
        _WARNED.add(message)
        print(f"jupyter-tts-alerts: {message}", file=sys.stderr)


_speaker: Optional[Speaker] = None


def get_speaker() -> Speaker:
    """The process-wide speaker, created on first use."""
    global _speaker
    if _speaker is None or _speaker._closed:
        _speaker = Speaker()
        atexit.register(_speaker.close)
    return _speaker


def missing_engine_hint() -> str:
    """What to tell somebody who has no speech engine installed at all."""
    hints = install_hints()
    joined = ", or ".join(hints) if hints else "install a speech engine"
    return f"no speech engine found; try: {joined}"
