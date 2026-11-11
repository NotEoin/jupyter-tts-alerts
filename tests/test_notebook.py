"""Runs the shipped notebooks headlessly, through a real kernel.

The verification notebook runs with the null engine. The point there is not the
audio (CI runners have no speech engine) but that every cell people are told to
run still executes, and that the only cells which fail are the two in the
Failures appendix that fail on purpose.

The example notebook runs with the recording espeak-ng shim instead, so what it
says is checked at the engine, in order and in the right voice, against
`EXAMPLE_SPEECH`; a second test holds the prose to that same list.
"""

from __future__ import annotations

import os
import pathlib
import re

import pytest

nbformat = pytest.importorskip("nbformat")
nbclient = pytest.importorskip("nbclient")

NOTEBOOKS = pathlib.Path(__file__).resolve().parent.parent / "notebooks"
VERIFY = NOTEBOOKS / "verify-alerts.ipynb"
EXAMPLE = NOTEBOOKS / "example.ipynb"

#: The appendix demonstrates a failing cell and an interrupt. Both are meant to
#: raise; anything else raising is a broken notebook.
DELIBERATE = ["ValueError", "KeyboardInterrupt"]

#: What the example says, as ``(alert voice, pattern)``, in order. Durations
#: depend on the machine; cell numbers and voices do not.
EXAMPLE_SPEECH = [
    (False, r"Starting notebook\."),
    (False, r"Cell 3\. \d+ seconds\."),
    (False, r"epoch 1 of 3 done"),
    (False, r"epoch 2 of 3 done"),
    (False, r"epoch 3 of 3 done"),
    (False, r"training finished"),
    (False, r"Cell 7\. \d+ seconds\."),
    (False, r"Notebook complete\. Total runtime \d+ seconds\."),
    (True, r"Error in cell 8\. Key error\."),
]


def run_notebook(path, drain=False):
    """Execute *path* in a fresh python3 kernel and return it with its outputs.

    Skips the calling test when no python3 kernelspec is installed. *drain*
    appends a silent cell that waits for queued speech, because kernel
    shutdown kills a speech process still running from the last cell.
    """
    from jupyter_client.kernelspec import NoSuchKernel

    notebook = nbformat.read(path, as_version=4)
    if drain:
        notebook.cells.append(nbformat.v4.new_code_cell(
            "# tts: off\nimport jupyter_tts_alerts\njupyter_tts_alerts.wait(60)"
        ))
    client = nbclient.NotebookClient(
        notebook,
        timeout=300,
        allow_errors=True,
        kernel_name="python3",
        resources={"metadata": {"path": str(path.parent)}},
    )
    try:
        client.execute()
    except NoSuchKernel:  # pragma: no cover - environment without a kernelspec
        pytest.skip("no python3 kernelspec available")
    return notebook


def errors_raised(notebook):
    """The exception names of every cell that failed, in order."""
    return [
        output["ename"]
        for cell in notebook.cells
        for output in cell.get("outputs", [])
        if output.get("output_type") == "error"
    ]


def markdown(path):
    """All the prose in the notebook at *path*, as one string."""
    notebook = nbformat.read(path, as_version=4)
    return "\n".join(c.source for c in notebook.cells if c.cell_type == "markdown")


@pytest.mark.skipif(not VERIFY.exists(), reason="notebook not present in this checkout")
def test_the_verification_notebook_still_runs(monkeypatch, tmp_path):
    from jupyter_tts_alerts.notify import NotifySend, OsascriptNotifier, ToastNotifier

    monkeypatch.setenv("JUPYTER_TTS_ENGINE", "none")   # keep CI silent
    # The notebook turns notifications on to demonstrate them, and the kernel
    # inherits this environment: without a stub for every platform's notifier,
    # running the tests pops a real notification on the developer's screen.
    stub = tmp_path / "bin"
    stub.mkdir()
    for notifier in (NotifySend, OsascriptNotifier, ToastNotifier):
        for exe in notifier.executables:
            if os.name == "nt":
                (stub / f"{exe}.bat").write_text("@exit /b 0\r\n")
            else:
                shim = stub / exe
                shim.write_text("#!/bin/sh\nexit 0\n")
                shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{stub}{os.pathsep}{os.environ['PATH']}")

    assert errors_raised(run_notebook(VERIFY)) == DELIBERATE


@pytest.mark.skipif(not VERIFY.exists(), reason="notebook not present in this checkout")
def test_the_verification_notebook_documents_what_you_should_hear():
    """Every check has to say what a working install sounds like."""
    prose = markdown(VERIFY)
    assert prose.count("You should hear") >= 8
    assert "pip install jupyter-tts-alerts" in prose


@pytest.mark.skipif(not EXAMPLE.exists(), reason="notebook not present in this checkout")
def test_the_example_notebook_says_what_it_promises(monkeypatch, shim):
    directory, log = shim
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("JTTS_SHIM_LOG", str(log))
    monkeypatch.setenv("JUPYTER_TTS_ENGINE", "espeak-ng")   # resolves to the shim

    # The last cell fails on purpose; nothing else may.
    assert errors_raised(run_notebook(EXAMPLE, drain=True)) == ["KeyError"]

    spoken = []
    for line in log.read_text().splitlines():
        options, separator, text = line.partition(" -- ")
        if separator:  # a voice listing would be logged too, with no message
            words = options.split()
            spoken.append((words[words.index("-v") + 1], text))
    heard = [(voice != spoken[0][0], text) for voice, text in spoken]
    # A str, because pytest truncates a list message after a few items, which
    # hides the late announcements that are the likeliest to go missing.
    transcript = "\n".join(
        f"{'alert ' if alert else 'normal'} {text}" for alert, text in heard
    )
    assert len(heard) == len(EXAMPLE_SPEECH), transcript
    for (alert, text), (wanted_alert, pattern) in zip(heard, EXAMPLE_SPEECH, strict=True):
        assert re.fullmatch(pattern, text) and alert == wanted_alert, transcript


@pytest.mark.skipif(not EXAMPLE.exists(), reason="notebook not present in this checkout")
def test_the_example_prose_promises_what_the_example_says():
    """Each quoted line under a bold "You should hear" matches `EXAMPLE_SPEECH`."""
    claims = re.findall(r"\*\*You should hear(.*?)\*\*", markdown(EXAMPLE), re.S)
    promised = [quote for claim in claims for quote in re.findall(r'"([^"]+)"', claim)]
    assert len(promised) == len(EXAMPLE_SPEECH), promised
    for quote, (_, pattern) in zip(promised, EXAMPLE_SPEECH, strict=True):
        assert re.fullmatch(pattern, quote), (quote, pattern)
