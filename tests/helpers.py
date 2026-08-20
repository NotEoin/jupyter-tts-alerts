"""Stand-ins used across the suite."""

from __future__ import annotations


class FakeSpeaker:
    """Records what would have been said instead of saying it."""

    def __init__(self) -> None:
        self.said = []
        self.notified = []

    def speak(self, text, alert=False):
        self.said.append((text, alert))
        return True

    def notify(self, title, body, urgent=False):
        self.notified.append((title, body, urgent))
        return True

    @property
    def lines(self):
        return [text for text, _ in self.said]

    @property
    def alerts(self):
        return [text for text, alert in self.said if alert]


class FakeInfo:
    """Stands in for IPython's ExecutionInfo."""

    def __init__(self, raw_cell="", silent=False):
        self.raw_cell = raw_cell
        self.silent = silent


class FakeResult:
    """Stands in for IPython's ExecutionResult.

    IPython builds one ExecutionInfo per execution and hands the *same object*
    to pre_run_cell and to ExecutionResult.info, which is how the announcer
    pairs the two events. Pass `info=` to model that; leave it out to model a
    post event with no matching pre, as an empty cell produces.
    """

    def __init__(self, raw_cell="", execution_count=1, error=None,
                 before_exec=False, silent=False, info=None):
        self.info = info if info is not None else FakeInfo(raw_cell, silent)
        self.execution_count = execution_count
        self.error_before_exec = error if before_exec else None
        self.error_in_exec = None if before_exec else error
