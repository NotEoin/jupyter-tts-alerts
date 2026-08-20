"""Runs the shipped verification notebook headlessly.

The point is not the audio (CI runners have no speech engine) but that every
cell in the notebook people are told to run still executes, and that the only
cells which fail are the two in the Failures appendix that fail on purpose.
"""

from __future__ import annotations

import os
import pathlib

import pytest

nbformat = pytest.importorskip("nbformat")
nbclient = pytest.importorskip("nbclient")

NOTEBOOK = pathlib.Path(__file__).resolve().parent.parent / "notebooks" / "verify-alerts.ipynb"

#: The appendix demonstrates a failing cell and an interrupt. Both are meant to
#: raise; anything else raising is a broken notebook.
DELIBERATE = ["ValueError", "KeyboardInterrupt"]


@pytest.mark.skipif(not NOTEBOOK.exists(), reason="notebook not present in this checkout")
def test_the_verification_notebook_still_runs(monkeypatch, tmp_path):
    from jupyter_client.kernelspec import NoSuchKernel

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
    notebook = nbformat.read(NOTEBOOK, as_version=4)
    client = nbclient.NotebookClient(
        notebook,
        timeout=300,
        allow_errors=True,
        kernel_name="python3",
        resources={"metadata": {"path": str(NOTEBOOK.parent)}},
    )
    try:
        client.execute()
    except NoSuchKernel:  # pragma: no cover - environment without a kernelspec
        pytest.skip("no python3 kernelspec available")

    raised = [
        output["ename"]
        for cell in notebook.cells
        for output in cell.get("outputs", [])
        if output.get("output_type") == "error"
    ]
    assert raised == DELIBERATE


@pytest.mark.skipif(not NOTEBOOK.exists(), reason="notebook not present in this checkout")
def test_the_notebook_documents_what_you_should_hear():
    """Every check has to say what a working install sounds like."""
    notebook = nbformat.read(NOTEBOOK, as_version=4)
    prose = "\n".join(c.source for c in notebook.cells if c.cell_type == "markdown")
    assert prose.count("You should hear") >= 8
    assert "pip install jupyter-tts-alerts" in prose
