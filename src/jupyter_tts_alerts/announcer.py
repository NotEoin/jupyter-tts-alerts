"""The IPython hooks: when to speak, about what, and in which voice.

Two events are enough. ``pre_run_cell`` starts a stopwatch; ``post_run_cell``
reports what happened, and IPython hands it the exception, so there is no need
to install a custom exception handler as well. Doing both is what made the
original script announce every failure twice under two different numbers.

The two events are also paired deliberately. ``post_run_cell`` fires in a
``finally`` block and reaches handlers in cases ``pre_run_cell`` never does:
an empty cell returns early before the pre event but still triggers the post
one, and a cell that was running when alerts were switched on has already
passed its pre event. Announcing those would report the *previous* cell's
stopwatch, so a cell is only ever announced if we saw it start.
"""

from __future__ import annotations

import io
import re
import time
import tokenize
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

from .config import get_config
from .speaker import Speaker, get_speaker
from .text import describe_fields, render

#: ``# tts: <something>``. Anchored to the start of a line, which is not the
#: restriction it looks like: :func:`_comment_text` puts every comment on a
#: line of its own first, so ``x = 1  # tts: off`` matches too. When a cell
#: will not tokenize the same anchor is what keeps a ``#`` inside a string
#: from being read as an instruction.
_DIRECTIVE = re.compile(r"^[ \t]*#[ \t]*tts[ \t]*:[ \t]*(?P<body>.*?)[ \t]*$", re.M)
#: The tags the pre-package script used, matched anywhere for compatibility.
_LEGACY_QUIET = "# noannounce"
_LEGACY_FINAL = "# notebook-complete"

_QUIET_WORDS = {"off", "quiet", "silent", "mute", "no", "skip", "none"}
_ALWAYS_WORDS = {"on", "always", "force", "yes"}
_FINAL_WORDS = {"final", "complete", "end", "done", "last"}

#: Depth past which we stop tracking nested executions.
MAX_NESTING = 64


@dataclass
class Directives:
    """What a cell asked for, in comments."""

    quiet: bool = False
    always: bool = False
    final: bool = False
    message: Optional[str] = None

    @property
    def forces_announcement(self) -> bool:
        """True when a directive asks for an announcement whatever the thresholds."""
        return self.always or self.final or self.message is not None


def _comment_text(source: str) -> str:
    """The cell's comments, and whether we managed to find them properly.

    Scanning raw lines treats a ``# tts: off`` inside a docstring or a
    generated-code template as an instruction. Tokenizing first means only
    real comments count, and it copes with cell magics, which tokenize
    perfectly well even though they are not valid Python.

    Some cells genuinely will not tokenize: an unterminated string, a stray
    NUL byte, a ``%%bash`` body that is not lexically Python. Those fall back
    to the raw source, where matching stays conservative because we can no
    longer tell a comment from the inside of a string.
    """
    try:
        return "\n".join(
            token.string
            for token in tokenize.generate_tokens(io.StringIO(source).readline)
            if token.type == tokenize.COMMENT
        )
    except Exception:
        # tokenize raises a genuine zoo: TokenError, IndentationError,
        # SyntaxError, ValueError, and SystemError for a NUL byte.
        return source


def parse_directives(source: str) -> Directives:
    """Read ``# tts:`` directives, plus the two tags from the old script."""
    found = Directives()
    if not source:
        return found
    comments = _comment_text(source)
    if _LEGACY_QUIET in comments:
        found.quiet = True
    if _LEGACY_FINAL in comments:
        found.final = True

    for match in _DIRECTIVE.finditer(comments):
        body = match.group("body").strip()
        if not body:
            continue
        words = body.split()
        first = words[0].lower().rstrip(".,")
        # A bare keyword is an instruction; a keyword followed by anything is
        # a sentence that happens to start with that word. Otherwise
        # `# tts: done training the model` would be silently swallowed as
        # `final` and never spoken. The one exception is a leading `say`, which
        # is always dropped, so that a sentence which is a keyword (`say done`)
        # can still be spoken.
        keyword = first if len(words) == 1 else ""
        if first == "say":
            found.message = _unquote(" ".join(words[1:])) or None
            found.always = True
        elif keyword in _QUIET_WORDS:
            found.quiet = True
        elif keyword in _ALWAYS_WORDS:
            found.always = True
        elif keyword in _FINAL_WORDS:
            found.final = True
        else:
            # Anything else is taken as the sentence to say. `# tts: model
            # finished` should not need a keyword to work.
            found.message = _unquote(body)
            found.always = True
    return found


def _unquote(text: str) -> str:
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1].strip()
    return text


class Announcer:
    """Holds the stopwatches and decides what is worth saying."""

    def __init__(self, speaker: Optional[Speaker] = None) -> None:
        self.speaker = speaker if speaker is not None else get_speaker()
        now = time.monotonic()
        self.notebook_start = now
        #: In-flight executions, as ``(info, started_at)``. A stack rather
        #: than a single stopwatch because ``%%capture``, ``%rerun`` and any
        #: user call to ``get_ipython().run_cell(...)`` nest one execution
        #: inside another, each firing its own pair of events.
        self._stack: List[Tuple[Any, float]] = []
        #: An exception raised by a nested execution. `%%capture` runs the
        #: cell body through an inner run_cell and does not re-raise what it
        #: catches, so the outer cell looks like a success; without this a
        #: failing captured cell would be announced as one.
        self._nested_error: Optional[BaseException] = None
        self._shell: Optional[Any] = None

    # -- wiring --
    @property
    def attached(self) -> bool:
        """True while the event handlers are registered."""
        return self._shell is not None

    def attach(self, shell: Any) -> "Announcer":
        """Register the two event handlers, replacing any earlier pair."""
        if self._shell is not None:
            self.detach()
        shell.events.register("pre_run_cell", self.pre_run_cell)
        shell.events.register("post_run_cell", self.post_run_cell)
        self._shell = shell
        self._forget_in_flight()
        return self

    def detach(self) -> None:
        """Unregister cleanly; tolerate handlers IPython has already dropped."""
        shell, self._shell = self._shell, None
        self._forget_in_flight()
        if shell is None:
            return
        for event, handler in (
            ("pre_run_cell", self.pre_run_cell),
            ("post_run_cell", self.post_run_cell),
        ):
            try:
                shell.events.unregister(event, handler)
            except (ValueError, KeyError):
                pass

    def reset(self) -> None:
        """Restart the notebook stopwatch, for when you resume a long run.

        Deliberately leaves the in-flight executions alone: ``reset()`` can
        only be called from inside a running cell, and forgetting that cell
        had started would silence the very announcement it was called for.
        """
        self.notebook_start = time.monotonic()

    # -- events --
    def pre_run_cell(self, info: Any) -> None:
        """Start a stopwatch for *info*, unless it is a silent execution."""
        if getattr(info, "silent", False):
            return
        if len(self._stack) >= MAX_NESTING:
            # Cannot happen through IPython, which always fires the matching
            # post event from a finally block. Bounded anyway, so that a
            # frontend which does not cannot grow this without limit.
            self._stack.pop(0)
        self._stack.append((info, time.monotonic()))

    def post_run_cell(self, result: Any) -> None:
        """Announce the cell that just finished, if it is worth announcing."""
        config = get_config()
        info = getattr(result, "info", None) if result is not None else None
        if info is not None and getattr(info, "silent", False):
            return

        started = self._take(info) if info is not None else None
        if started is None and info is not None and not (
            getattr(info, "raw_cell", "") or ""
        ).strip():
            # A blank cell: IPython returns before firing pre for it, so there
            # is nothing of its own to drop. Dropping anyway would throw away
            # the cell around it when it was run from inside another cell.
            return
        if started is None:
            # A post event we cannot pair. Either no pre fired for it -- an
            # empty cell, which IPython returns from before triggering pre --
            # or IPython built a fresh result after an exception escaped, or
            # handed us None outright. IPython fires exactly one post per pre
            # from a finally, so an entry we could not match will never be
            # matched: drop the newest, or the stack would keep it forever and
            # every later cell would look like a nested one and stay silent.
            self._drop_newest()
            if not self._stack:
                # Nothing is in flight any more, so an inner failure recorded
                # on the way belongs to no cell that is still to finish.
                self._nested_error = None
            return
        if self._stack:
            # An inner execution of a nested run_cell. The cell the user
            # actually ran is still going, and reporting this one would
            # announce the outer cell's number a cell too early. Its failure
            # still matters, though -- `%%capture` swallows it.
            self._nested_error = self._nested_error or _error_of(result)
            return
        # The outermost cell has finished. An inner failure belongs to it and
        # to no later cell, whichever of the returns below this cell takes.
        nested, self._nested_error = self._nested_error, None
        if not config.enabled:
            return

        directives = parse_directives(getattr(info, "raw_cell", "") or "")
        if directives.quiet:
            return

        elapsed = max(0.0, time.monotonic() - started)
        total = max(0.0, time.monotonic() - self.notebook_start)
        error = _error_of(result) or nested
        cell = getattr(result, "execution_count", None)
        fields = describe_fields(elapsed, cell, error, total)

        if error is not None:
            self._announce_error(config, directives, error, elapsed, cell, fields)
        else:
            self._announce_success(config, directives, elapsed, cell, fields)

    def _take(self, info: Any) -> Optional[float]:
        """Pop the start time recorded for *info*, or None if we never saw it."""
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] is info:
                return self._stack.pop(index)[1]
        return None

    def _drop_newest(self) -> None:
        """Discard the most recent in-flight execution, whatever it was."""
        if self._stack:
            self._stack.pop()

    def _forget_in_flight(self) -> None:
        self._stack.clear()
        self._nested_error = None

    # -- decisions --
    def _announce_error(self, config, directives, error, elapsed, cell, fields) -> None:
        if not config.announce_errors:
            return
        # An interrupt means you pressed the button yourself; you know already.
        if isinstance(error, KeyboardInterrupt) and not config.announce_interrupts:
            return
        if elapsed < config.min_error_seconds and not directives.forces_announcement:
            return
        self.speaker.speak(render(config.phrase("error"), **fields), alert=True)
        if config.notify:
            self.speaker.notify(
                _cell_label(cell, "failed"),
                f"{type(error).__name__}: {error}",
                urgent=True,
            )

    def _announce_success(self, config, directives, elapsed, cell, fields) -> None:
        wanted = config.announce_cells or directives.forces_announcement
        long_enough = elapsed >= config.min_seconds or directives.forces_announcement
        if wanted and long_enough:
            template = directives.message or config.phrase("cell")
            self.speaker.speak(render(template, **fields))
            if config.notify:
                self.speaker.notify(
                    _cell_label(cell, "finished"), f"Took {fields['duration']}."
                )
        if directives.final:
            self.speaker.speak(render(config.phrase("complete"), **fields))
            if config.notify:
                self.speaker.notify(
                    "Notebook complete", f"Total runtime {fields['total']}."
                )


def _error_of(result: Any) -> Optional[BaseException]:
    """Whichever of the two failure slots IPython filled in, if either."""
    return getattr(result, "error_before_exec", None) or getattr(
        result, "error_in_exec", None
    )


def _cell_label(cell: Any, verb: str) -> str:
    return f"Cell {cell} {verb}" if cell is not None else f"Cell {verb}"
