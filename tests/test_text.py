"""Durations, exception names and templates rendered as speech."""

import pytest

from jupyter_tts_alerts.text import (
    describe_fields,
    format_duration,
    humanise_exception,
    render,
    sanitise,
)


@pytest.mark.parametrize(
    "seconds, expected",
    [
        (0, "less than a second"),
        (0.9, "less than a second"),
        (1, "1 second"),
        (1.9, "1 second"),
        (2, "2 seconds"),
        (59, "59 seconds"),
        (60, "1 minute"),
        (61, "1 minute 1 second"),
        (125, "2 minutes 5 seconds"),
        (3600, "1 hour"),
        (3725, "1 hour 2 minutes 5 seconds"),
        (7322, "2 hours 2 minutes 2 seconds"),
        (-5, "less than a second"),
    ],
)
def test_durations_are_spoken_in_whole_units(seconds, expected):
    assert format_duration(seconds) == expected


def test_format_duration_survives_nonsense():
    assert format_duration(float("nan")) == "an unknown length of time"
    assert format_duration(float("inf")) == "an unknown length of time"


@pytest.mark.parametrize(
    "exc, expected",
    [
        (ValueError("x"), "value error"),
        (KeyError("x"), "key error"),
        (KeyboardInterrupt(), "keyboard interrupt"),
        (ZeroDivisionError(), "zero division error"),
        (None, "error"),
    ],
)
def test_exception_classes_are_spoken_as_words(exc, expected):
    assert humanise_exception(exc) == expected


def test_humanise_keeps_acronyms_intact():
    class HTTPError(Exception):
        pass

    # Runs of capitals stay together so engines spell them out as letters.
    assert humanise_exception(HTTPError()) == "HTTP error"


def test_sanitise_collapses_and_truncates():
    assert sanitise("  hello\n\tworld  ") == "hello world"
    assert sanitise("\x07bell") == "bell"
    assert len(sanitise("x" * 500)) == 300


def test_sanitise_leaves_a_leading_dash_alone():
    # Argv safety is the engines' job now (they pass "--"), not a text hack.
    assert sanitise("-v is not a message") == "-v is not a message"


def test_sanitise_tidies_orphaned_punctuation():
    # What "Cell {cell}." renders to when there is no execution count.
    assert sanitise("Cell . 3 seconds.") == "Cell. 3 seconds."


def test_error_type_is_sentence_cased_without_wrecking_acronyms():
    class HTTPError(Exception):
        pass

    assert describe_fields(1, 1, ValueError(), 1)["error_type"] == "Value error"
    assert describe_fields(1, 1, HTTPError(), 1)["error_type"] == "HTTP error"


def test_render_tolerates_unknown_fields():
    assert render("Cell {cell} in {nope}.", cell=3) == "Cell 3 in {nope}."


def test_render_tolerates_broken_templates():
    assert render("Cell {", cell=3) == "Cell {"


def test_describe_fields_covers_every_documented_name():
    fields = describe_fields(65.0, 7, ValueError("bad"), 120.0)
    assert fields["cell"] == 7
    assert fields["duration"] == "1 minute 5 seconds"
    assert fields["seconds"] == 65.0
    assert fields["total"] == "2 minutes"
    assert fields["error_type"] == "Value error"   # starts a sentence
    assert fields["error"] == "bad"
