"""The `# tts:` directives a cell can carry, and where they are ignored."""

import pytest

from jupyter_tts_alerts.announcer import parse_directives


def test_a_plain_cell_asks_for_nothing():
    found = parse_directives("import pandas as pd\ndf.head()")
    assert not (found.quiet or found.always or found.final)
    assert found.message is None
    assert found.forces_announcement is False


@pytest.mark.parametrize("word", ["off", "quiet", "silent", "mute", "no", "skip"])
def test_every_quiet_synonym_silences_the_cell(word):
    assert parse_directives(f"# tts: {word}\nwork()").quiet is True


@pytest.mark.parametrize("word", ["on", "always", "force", "yes"])
def test_every_always_synonym_forces_an_announcement(word):
    found = parse_directives(f"# tts: {word}\nwork()")
    assert found.always is True and found.forces_announcement


@pytest.mark.parametrize("word", ["final", "complete", "end", "done", "last"])
def test_every_final_synonym_marks_the_last_cell(word):
    assert parse_directives(f"# tts: {word}\nwork()").final is True


def test_legacy_tags_from_the_original_script_still_work():
    assert parse_directives("x = 1  # noannounce").quiet is True
    assert parse_directives("x = 1  # notebook-complete").final is True


def test_say_directive_sets_the_message_and_forces_it():
    found = parse_directives('# tts: say "training finished"\ntrain()')
    assert found.message == "training finished"
    assert found.always is True


def test_a_bare_sentence_is_taken_as_the_message():
    found = parse_directives("# tts: the model has finished training\ntrain()")
    assert found.message == "the model has finished training"
    assert found.forces_announcement


def test_directives_are_found_anywhere_in_the_cell():
    found = parse_directives("import time\ntime.sleep(1)\n# tts: final\n")
    assert found.final is True


def test_spacing_and_case_are_forgiven():
    assert parse_directives("   #tts:OFF").quiet is True
    assert parse_directives("#  tts  :  Always ").always is True


def test_a_directive_inside_a_string_is_ignored():
    # Only whole comment lines count, so this stays a plain fast cell.
    found = parse_directives('print("# tts: off")')
    assert found.quiet is False


def test_a_directive_inside_a_docstring_is_ignored():
    """The line-anchored regex alone would honour this one."""
    source = 'TEMPLATE = """\nusage:\n# tts: off\n"""\ntrain()'
    assert parse_directives(source).quiet is False


def test_a_legacy_tag_inside_a_string_is_ignored():
    assert parse_directives('doc = "# noannounce"\ntrain()').quiet is False


def test_directives_work_in_a_cell_magic():
    """Cell magics are not valid Python, but they do tokenize."""
    assert parse_directives("%%capture\n# tts: off\ntrain()").quiet is True
    assert parse_directives("%%capture\ntrain()  # tts: off").quiet is True


def test_directives_still_work_in_a_cell_that_will_not_tokenize():
    """An unterminated string defeats tokenize; the raw scan takes over."""
    assert parse_directives('# tts: off\ntext = "unterminated').quiet is True


def test_the_fallback_scan_stays_conservative():
    """Without tokens we cannot tell a comment from the inside of a string."""
    source = '%%bash\necho "# tts: off"\ntext = "unterminated'
    assert parse_directives(source).quiet is False


def test_a_nul_byte_does_not_crash_the_parser():
    """tokenize raises SystemError, not any of the exceptions you would guess."""
    assert parse_directives("x = 1\x00\n# tts: off").quiet is True


def test_a_sentence_starting_with_a_keyword_is_still_a_sentence():
    found = parse_directives("# tts: done training the model\ntrain()")
    assert found.message == "done training the model"
    assert found.final is False


def test_a_bare_keyword_is_still_a_keyword():
    assert parse_directives("# tts: done").final is True
    assert parse_directives("# tts: done.").final is True


def test_a_directive_may_trail_a_line_of_code():
    assert parse_directives("x = 1  # tts: off").quiet is True
    assert parse_directives("train()  # tts: final").final is True


def test_several_directives_combine():
    found = parse_directives("# tts: final\n# tts: say all done\nwork()")
    assert found.final is True and found.message == "all done"


def test_empty_source_is_safe():
    assert parse_directives("").message is None


def test_a_non_python_cell_body_honours_a_directive_on_its_own_line():
    """The documented limit: in such a body, only a line of its own is seen."""
    assert parse_directives("%%bash\necho It\\'s done\n# tts: off").quiet is True
    assert parse_directives("%%bash\necho It's done  # tts: off").quiet is False


def test_say_lets_a_keyword_be_spoken_as_the_sentence():
    found = parse_directives("# tts: say done\nwork()")
    assert (found.message, found.final) == ("done", False)
