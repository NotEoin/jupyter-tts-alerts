import subprocess
import shutil
import time
import traceback
from IPython import get_ipython

# ============================================================
# Jupyter Notebook TTS Announcer
# ============================================================
# Speaks cell numbers, runtimes, errors, and notebook completion
# using text-to-speech. Requires espeak-ng, espeak, or spd-say.
#
# GLOBAL CONTROLS
#   ANNOUNCEMENTS_ENABLED = False   — silence everything
#   TTS_VOLUME             = 50     — volume (0–200)
#   MIN_RUNTIME_ANNOUNCE   = 5.0    — minimum seconds before a
#                                     successful cell is announced
#                                     (errors always announce)
#
# PER-CELL TAGS (place anywhere in the cell source)
#   # noannounce        — silence this cell
#   # notebook-complete — announce total notebook runtime after this cell
# ============================================================

ANNOUNCEMENTS_ENABLED  = True
TTS_VOLUME             = 50
MIN_RUNTIME_ANNOUNCE   = 5.0
DISABLE_TAG            = "# noannounce"
COMPLETE_TAG           = "# notebook-complete"

# ── TTS engine ───────────────────────────────────────────────
TTS_CMD = next(
    (cmd for cmd in ("espeak-ng", "espeak", "spd-say") if shutil.which(cmd)),
    None
)
if TTS_CMD is None:
    print("⚠️  No TTS engine found. Install espeak-ng, espeak, or spd-say.")

# ── Voice selection ───────────────────────────────────────────
def _pick_voice(gender):
    """Return the first available voice of the requested gender (m/f)."""
    if TTS_CMD not in ("espeak-ng", "espeak"):
        return "default"
    tag = f"+{gender}"
    try:
        out = subprocess.check_output([TTS_CMD, "--voices=en"], text=True)
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 4 and tag in parts[3].lower():
                return parts[3]
    except Exception:
        pass
    return f"en+{gender}1"   # sensible fallback

MALE_VOICE   = _pick_voice("m")
FEMALE_VOICE = _pick_voice("f")

# ── Speech ───────────────────────────────────────────────────
_current_speech = None

def _speak(text, voice=None):
    global _current_speech
    if not ANNOUNCEMENTS_ENABLED or TTS_CMD is None:
        return
    if _current_speech is not None and _current_speech.poll() is None:
        _current_speech.wait()
    try:
        if TTS_CMD in ("espeak-ng", "espeak"):
            _current_speech = subprocess.Popen(
                [TTS_CMD, "-v", voice or MALE_VOICE, "-a", str(TTS_VOLUME), text]
            )
        else:
            _current_speech = subprocess.Popen([TTS_CMD, text])
    except Exception as e:
        print(f"⚠️  TTS failed: {e}")

def _format_duration(seconds):
    seconds = int(seconds)
    h, remainder = divmod(seconds, 3600)
    m, s = divmod(remainder, 60)
    parts = []
    if h: parts.append(f"{h} hour{'s' if h != 1 else ''}")
    if m: parts.append(f"{m} minute{'s' if m != 1 else ''}")
    if s or not parts: parts.append(f"{s} second{'s' if s != 1 else ''}")
    return " ".join(parts)

# ── State ─────────────────────────────────────────────────────
_state = {
    "cell_count":    0,
    "cell_start":    0.0,
    "notebook_start": time.time(),
    "started":       False,
    "skip":          False,
    "is_final":      False,
}

# ── Event handlers ────────────────────────────────────────────
def _pre_run_cell(info):
    src = getattr(info, "raw_cell", "")
    _state["skip"]     = DISABLE_TAG  in src
    _state["is_final"] = COMPLETE_TAG in src
    _state["cell_start"] = time.time()

    if not _state["started"] and ANNOUNCEMENTS_ENABLED:
        _speak("Starting notebook.")
        _state["started"] = True


def _post_run_cell(result):
    if _state["skip"] or not ANNOUNCEMENTS_ENABLED:
        return

    _state["cell_count"] += 1
    num     = _state["cell_count"]
    elapsed = time.time() - _state["cell_start"]

    # Error detection — always announce regardless of runtime
    error = (
        (hasattr(result, "error_before_exec") and result.error_before_exec) or
        (hasattr(result, "error_in_exec")     and result.error_in_exec)
    )
    if error:
        _speak(f"Error in cell {num}.", voice=FEMALE_VOICE)
        return

    # Skip fast cells (unless this is the final cell)
    if elapsed < MIN_RUNTIME_ANNOUNCE and not _state["is_final"]:
        return

    _speak(f"Cell {num}. {_format_duration(elapsed)}.")

    if _state["is_final"]:
        total = time.time() - _state["notebook_start"]
        _speak(f"Notebook complete. Total runtime {_format_duration(total)}.")


def _on_error(exc_type, exc, tb):
    traceback.print_exception(exc_type, exc, tb)
    if _state["skip"] or not ANNOUNCEMENTS_ENABLED:
        return
    _state["cell_count"] += 1
    _speak(f"Error in cell {_state['cell_count']}.", voice=FEMALE_VOICE)


# ── Register ──────────────────────────────────────────────────
ip = get_ipython()
ip.events.register("pre_run_cell",  _pre_run_cell)
ip.events.register("post_run_cell", _post_run_cell)
ip.set_custom_exc((Exception,), _on_error)

print(f"🔊 TTS announcer ready  |  engine: {TTS_CMD}  |  male: {MALE_VOICE}  |  female (errors): {FEMALE_VOICE}")
