"""Working out whether anyone is actually going to hear this.

The package speaks on the machine the *kernel* runs on. That is the same
machine as your ears surprisingly often, and when it isn't, no amount of
volume will help. Rather than fail silently, we look for the usual signs of a
kernel living somewhere else and say so once, at startup.
"""

from __future__ import annotations

import os
import platform
from typing import List, Mapping, Optional

#: Environment variables that give the game away, and what they mean.
REMOTE_SIGNALS = (
    ("SSH_CONNECTION", "the kernel is on the far end of an SSH connection"),
    ("SSH_CLIENT", "the kernel is on the far end of an SSH connection"),
    ("JUPYTERHUB_USER", "the kernel is running under JupyterHub"),
    ("COLAB_RELEASE_TAG", "the kernel is running on Google Colab"),
    ("COLAB_GPU", "the kernel is running on Google Colab"),
    ("BINDER_SERVICE_HOST", "the kernel is running on Binder"),
    ("CODESPACES", "the kernel is running in a GitHub Codespace"),
    ("SPACE_ID", "the kernel is running on a Hugging Face Space"),
    ("KUBERNETES_SERVICE_HOST", "the kernel is running inside Kubernetes"),
    ("KAGGLE_KERNEL_RUN_TYPE", "the kernel is running on Kaggle"),
    ("DATABRICKS_RUNTIME_VERSION", "the kernel is running on Databricks"),
    ("SM_CURRENT_HOST", "the kernel is running on SageMaker"),
)
# Deliberately not checked: VSCODE_* and TERM_PROGRAM. Those describe the
# terminal that happened to launch something, are not reliably inherited by
# the Jupyter extension's kernel, and are set just as often on the laptop you
# are sitting in front of.


def has_audio_device(environ: Optional[Mapping[str, str]] = None) -> bool:
    """Best-effort check for a sound card. Only meaningful on Linux."""
    if platform.system() != "Linux":
        return True
    environ = os.environ if environ is None else environ
    if environ.get("PULSE_SERVER"):
        return True
    try:
        return bool(os.listdir("/dev/snd"))
    except OSError:
        return False


def remote_reasons(environ: Optional[Mapping[str, str]] = None) -> List[str]:
    """Every reason to suspect nobody is sitting next to this kernel."""
    environ = os.environ if environ is None else environ
    reasons: List[str] = []
    for name, explanation in REMOTE_SIGNALS:
        if environ.get(name) and explanation not in reasons:
            reasons.append(explanation)
    if not has_audio_device(environ):
        reasons.append("no sound device is visible to this process")
    return reasons


def looks_remote(environ: Optional[Mapping[str, str]] = None) -> bool:
    """True when speech is likely to be played into an empty room."""
    return bool(remote_reasons(environ))
