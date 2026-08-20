"""Speech engines.

Each backend is a thin description of how to drive one command-line
synthesiser: how to say a string, how to list its voices, and how to pick two
voices that sound different enough that you can tell success from failure
without listening to the words.

Two things every backend here gets right, because getting them wrong is
quiet and awful:

* **The message is never part of a shell string.** Every engine is invoked
  with an argument list, or handed its text out of band.
* **The message is separated from the options with ``--``.** Exception text
  and forced announcements start with a minus sign more often than you would
  think, and ``spd-say "-100 percent failure"`` exits 1 with
  ``invalid option -- '1'`` rather than saying anything.

Volume is a single 0-100 knob across every backend, where **50 means the
engine's own normal level**, below that is quieter and above it is louder
where the engine has the headroom. The engines disagree wildly about scales
(espeak runs 0-200 with a default of 100, speech-dispatcher runs -100 to +100 with
a default of 0, SAPI runs 0-100 with a default of 100), so a raw number would
mean something different everywhere.

Nothing here plays audio itself and nothing here imports anything outside the
standard library. :mod:`jupyter_tts_alerts.speaker` does the running.
"""

from __future__ import annotations

import base64
import platform
import re
import shutil
import subprocess
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple, Type

from .text import sanitise

#: How long we will wait for a voice-listing subprocess before giving up.
PROBE_TIMEOUT = 5.0

#: espeak-ng(1) and macOS say(1) both speak at 175 words per minute by default.
DEFAULT_WPM = 175
#: Words per minute added per point of ``rate``, so -100..100 spans about 100-250.
WPM_PER_RATE = 0.75
#: espeak-ng's own speed limits (espeakRATE_MINIMUM / MAXIMUM in speak_lib.h).
ESPEAK_WPM_RANGE = (80, 450)
#: Bounds kept for ``say``, which documents none; unverified on real hardware.
SAY_WPM_RANGE = (60, 500)
#: SAPI's SpeechSynthesizer.Rate runs -10..10, so ten of our points make one of its.
SAPI_RATE_STEP = 10

#: Every ``[`` that is immediately followed by another one.
_SPLIT_BRACKETS = re.compile(r"\[(?=\[)")


class Command(NamedTuple):
    """A ready-to-run invocation: argv, optional stdin, optional extra env."""

    argv: List[str]
    stdin: Optional[str] = None
    env: Optional[Dict[str, str]] = None
    #: Speech engines lie about their exit status -- spd-say reports success
    #: for an invalid voice, an invalid volume and an unknown synthesis voice
    #: alike -- so a non-zero exit is only worth reporting where it means
    #: something. Notifications set this; speech does not.
    warn_on_failure: bool = False


def _clamp(value: float, low: float, high: float) -> int:
    return int(max(low, min(high, value)))


def encode_powershell(script: str) -> List[str]:
    """Arguments that run *script* under PowerShell, immune to quoting.

    ``-EncodedCommand`` takes base64 of UTF-16LE, so no shell, no argv
    escaping layer and no locale can mangle what actually gets executed.
    """
    payload = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return ["-NoProfile", "-NonInteractive", "-EncodedCommand", payload]


class Engine:
    """Base class. Subclasses describe one synthesiser."""

    #: Name used in ``configure(engine=...)`` and shown by ``status()``.
    name: str = ""
    #: Executable(s) to look for, in order of preference.
    executables: Sequence[str] = ()
    #: True when the engine ships with the operating system, so there is no
    #: install command to suggest -- only a reason it is missing.
    bundled: bool = False
    #: ``(normal, alert)`` voices cached by :class:`Speaker` on first use, so
    #: a voice listing is probed once per engine.
    _cached_default_voices: Optional[Tuple[Optional[str], Optional[str]]] = None

    @classmethod
    def install_hint(cls) -> str:
        """A command that would install this engine *on this platform*.

        Empty when there is nothing useful to say: the engine is bundled, or
        it does not exist here at all. Telling a Windows user to run
        ``sudo apt install`` is worse than telling them nothing.
        """
        return ""

    def __init__(self, path: str) -> None:
        self.path = path

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} {self.path}>"

    # -- discovery --
    @classmethod
    def find(cls) -> Optional["Engine"]:
        """Locate the executable. Never runs it, because import must stay cheap."""
        for exe in cls.executables:
            path = shutil.which(exe)
            if path:
                return cls(path)
        return None

    # -- the two things an engine must do --
    def speak_command(self, text: str, voice: Optional[str], volume: int,
                      rate: int) -> Command:
        """The `Command` that says *text*; an empty argv means say nothing."""
        raise NotImplementedError

    def voices(self) -> List[str]:
        """Voice names accepted by ``configure(voice=...)``. May be empty."""
        return []

    # -- niceties --
    def default_voices(self) -> Tuple[Optional[str], Optional[str]]:
        """``(normal, alert)``: two voices that should sound distinct."""
        return None, None

    def cancel_command(self) -> Optional[Command]:
        """How to stop speech already queued inside a daemon, if it can be."""
        return None

    def _probe(self, args: Sequence[str]) -> str:
        """Run a short listing command, returning ``""`` on any trouble."""
        try:
            done = subprocess.run(
                [self.path, *args],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=PROBE_TIMEOUT,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        return done.stdout.decode("utf-8", "replace")


class EspeakEngine(Engine):
    """espeak-ng / espeak: the fastest to start and the easiest to vary."""

    name = "espeak-ng"
    executables = ("espeak-ng", "espeak")

    @classmethod
    def install_hint(cls) -> str:
        system = platform.system()
        if system == "Darwin":
            return "brew install espeak-ng"
        if system == "Windows":
            return "install espeak-ng from github.com/espeak-ng/espeak-ng/releases"
        return "sudo apt install espeak-ng"

    def speak_command(self, text: str, voice: Optional[str], volume: int,
                      rate: int) -> Command:
        argv = [
            self.path,
            "-a", str(_clamp(volume * 2, 0, 200)),          # 0-200, default 100
            "-s", str(_clamp(DEFAULT_WPM + rate * WPM_PER_RATE, *ESPEAK_WPM_RANGE)),
        ]
        if voice:
            argv += ["-v", voice]
        argv += ["--", sanitise(text)]
        return Command(argv)

    def voices(self) -> List[str]:
        out = self._probe(["--voices=en"]) or self._probe(["--voices"])
        found = []
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 4:
                found.append(parts[1])          # the Language column
        return found

    def default_voices(self) -> Tuple[Optional[str], Optional[str]]:
        # espeak's +mN / +fN variants are built in, so this works even when
        # the voice listing cannot be read.
        base = "en"
        for candidate in self.voices():
            if candidate == "en":
                break
            if candidate.startswith("en"):
                base = candidate
                break
        return f"{base}+m3", f"{base}+f3"


class SpdSayEngine(Engine):
    """speech-dispatcher's client. Present on most desktop Linux installs."""

    name = "spd-say"
    executables = ("spd-say",)

    @classmethod
    def install_hint(cls) -> str:
        # speech-dispatcher is a Linux desktop component; suggesting it
        # anywhere else would just be noise.
        return (
            "sudo apt install speech-dispatcher"
            if platform.system() == "Linux"
            else ""
        )

    #: The symbolic voices spd-say takes via ``-t``. These are the only ones
    #: worth offering: ``-t`` rejects a bad name loudly, while ``-y`` accepts
    #: anything, exits 0 and quietly speaks in the default voice instead.
    VOICE_TYPES = (
        "male1", "male2", "male3", "female1", "female2", "female3",
        "child_male", "child_female",
    )

    def speak_command(self, text: str, voice: Optional[str], volume: int,
                      rate: int) -> Command:
        argv = [
            self.path,
            "-w",                                        # wait until spoken
            "-N", "jupyter-tts-alerts",                  # shows up in logs
            "-i", str(_clamp(volume * 2 - 100, -100, 100)),  # -100-+100, def 0
            "-r", str(_clamp(rate, -100, 100)),
        ]
        if voice:
            argv += ["-t" if voice in self.VOICE_TYPES else "-y", voice]
        argv += ["--", sanitise(text)]
        return Command(argv)

    def voices(self) -> List[str]:
        # `spd-say -L` lists close to fifteen thousand synthesis voices in
        # fixed-width columns that collide for long names. Offering the eight
        # symbolic types is more useful than a wall of unparseable text; a
        # specific synthesis voice can still be set by name and is passed to -y.
        return list(self.VOICE_TYPES)

    def default_voices(self) -> Tuple[Optional[str], Optional[str]]:
        return "male1", "female1"

    # Deliberately no cancel_command. `spd-say -C` issues SSIP CANCEL ALL,
    # which discards the queued speech of *every* client on the machine --
    # including Orca, which reads the screen aloud for blind users through
    # the same daemon. spd-say offers no way to cancel just our own messages,
    # and killing our client process is close enough: the daemon finishes the
    # one-line utterance already in flight, and nothing else is queued.


class SayEngine(Engine):
    """macOS ``say``. Always present, no install step."""

    name = "say"
    executables = ("say",)
    bundled = True

    def speak_command(self, text: str, voice: Optional[str], volume: int,
                      rate: int) -> Command:
        wpm = _clamp(DEFAULT_WPM + rate * WPM_PER_RATE, *SAY_WPM_RANGE)
        argv = [self.path, "-r", str(wpm)]
        if voice:
            argv += ["-v", voice]
        # `say` has no volume flag; the inline speech command is the documented
        # route, and it doubles as protection against a leading minus sign,
        # since the argument now always starts with a bracket.
        level = min(1.0, max(0.0, volume / 50))
        # `[[` opens an embedded speech command, so a message containing one
        # would be interpreted rather than spoken -- and truncation can leave
        # an unbalanced opener behind. Splitting the bracket keeps the text
        # audible on voices that ignore the commands entirely.
        # A plain replace scans left to right without overlapping, so "[[["
        # would come out as "[ [[" -- still a live command opener, and a
        # three-dimensional numpy repr is exactly the sort of message that
        # gets read out. Splitting every "[" that precedes another cannot
        # leave a pair behind.
        spoken = _SPLIT_BRACKETS.sub("[ ", sanitise(text))
        argv.append(f"[[volm {level:.2f}]] {spoken}")
        return Command(argv)

    def voices(self) -> List[str]:
        found = []
        for line in self._probe(["-v", "?"]).splitlines():
            name, _, _rest = line.partition("#")
            tokens = name.split()
            if len(tokens) >= 2:
                found.append(" ".join(tokens[:-1]))
        return found

    def default_voices(self) -> Tuple[Optional[str], Optional[str]]:
        available = self.voices()
        preferred = [v for v in ("Alex", "Samantha", "Daniel", "Karen") if v in available]
        if len(preferred) >= 2:
            return preferred[0], preferred[1]
        if len(available) >= 2:
            return available[0], available[1]
        return None, None


class SapiEngine(Engine):
    """Windows SAPI, driven through PowerShell.

    The script is passed base64-encoded so no quoting layer can mangle it, and
    the text arrives in an environment variable rather than on stdin, because Windows
    PowerShell 5.1 reads stdin in the OEM codepage and would garble anything
    outside ASCII.
    """

    name = "sapi"
    executables = ("powershell", "pwsh")
    bundled = True

    _SPEAK = (
        "$ErrorActionPreference='Stop';"
        "Add-Type -AssemblyName System.Speech;"
        "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        "$s.Volume=[int]$env:JTTS_VOLUME;"
        "$s.Rate=[int]$env:JTTS_RATE;"
        "if($env:JTTS_VOICE){try{$s.SelectVoice($env:JTTS_VOICE)}catch{}};"
        "$s.Speak($env:JTTS_TEXT)"
    )
    _LIST = (
        "Add-Type -AssemblyName System.Speech;"
        "(New-Object System.Speech.Synthesis.SpeechSynthesizer)"
        ".GetInstalledVoices()|ForEach-Object{"
        '"$($_.VoiceInfo.Gender)`t$($_.VoiceInfo.Name)"}'
    )

    def _invoke(self, script: str) -> List[str]:
        return [self.path, *encode_powershell(script)]

    def speak_command(self, text: str, voice: Optional[str], volume: int,
                      rate: int) -> Command:
        return Command(
            self._invoke(self._SPEAK),
            env={
                "JTTS_TEXT": sanitise(text),
                # SAPI's own default is 100, so 50 on our scale must map there.
                "JTTS_VOLUME": str(_clamp(volume * 2, 0, 100)),
                "JTTS_RATE": str(_clamp(round(rate / SAPI_RATE_STEP), -10, 10)),
                "JTTS_VOICE": voice or "",
            },
        )

    def _installed(self) -> List[Tuple[str, str]]:
        rows = []
        for line in self._probe(self._invoke(self._LIST)[1:]).splitlines():
            gender, tab, name = line.partition("\t")
            if tab and name.strip():
                rows.append((gender.strip(), name.strip()))
        return rows

    def voices(self) -> List[str]:
        return [name for _, name in self._installed()]

    def default_voices(self) -> Tuple[Optional[str], Optional[str]]:
        rows = self._installed()
        males = [n for g, n in rows if g == "Male"]
        females = [n for g, n in rows if g == "Female"]
        if males and females:
            return males[0], females[0]
        names = [n for _, n in rows]
        return (names[0], names[1]) if len(names) >= 2 else (None, None)


class TermuxEngine(Engine):
    """Android under Termux, via termux-api."""

    name = "termux"
    executables = ("termux-tts-speak",)

    @classmethod
    def install_hint(cls) -> str:
        return "pkg install termux-api"

    def speak_command(self, text: str, voice: Optional[str], volume: int,
                      rate: int) -> Command:
        # Reads the message on stdin, so nothing needs escaping and there is
        # no argv to confuse. Volume and rate are not exposed by the wrapper.
        return Command([self.path], stdin=sanitise(text))


class NullEngine(Engine):
    """Selected with ``configure(engine="none")``: never makes a sound."""

    name = "none"
    executables = ()

    def __init__(self, path: str = "") -> None:
        super().__init__(path)

    @classmethod
    def find(cls) -> "NullEngine":
        return cls()

    def speak_command(self, text: str, voice: Optional[str], volume: int,
                      rate: int) -> Command:
        return Command([])


#: Every engine that can be named in ``configure(engine=...)``.
REGISTRY: Dict[str, Type[Engine]] = {
    EspeakEngine.name: EspeakEngine,
    "espeak": EspeakEngine,
    SpdSayEngine.name: SpdSayEngine,
    SayEngine.name: SayEngine,
    SapiEngine.name: SapiEngine,
    TermuxEngine.name: TermuxEngine,
    NullEngine.name: NullEngine,
}


def preference_order() -> List[Type[Engine]]:
    """Which engines to try, best first, for the platform we are on."""
    system = platform.system()
    if system == "Darwin":
        return [SayEngine, EspeakEngine, SpdSayEngine]
    if system == "Windows":
        return [SapiEngine, EspeakEngine]
    return [EspeakEngine, SpdSayEngine, TermuxEngine]


def detect_engine() -> Optional[Engine]:
    """First working engine for this platform, or ``None`` if there is none."""
    for engine_cls in preference_order():
        engine = engine_cls.find()
        if engine is not None:
            return engine
    return None


def load_engine(name: str) -> Engine:
    """Build a named engine, raising if it is unknown or not installed."""
    try:
        engine_cls = REGISTRY[name]
    except KeyError:
        raise ValueError(
            "unknown engine %r (choose from %s, or None to auto-detect)"
            % (name, ", ".join(sorted(REGISTRY)))
        ) from None
    engine = engine_cls.find()
    if engine is None:
        hint = engine_cls.install_hint()
        if hint:
            raise RuntimeError(f"engine {name!r} is not installed; try: {hint}")
        if engine_cls.bundled:
            raise RuntimeError(
                f"engine {name!r} ships with its operating system, and is not "
                "on this one"
            )
        raise RuntimeError(f"engine {name!r} is not installed")
    return engine


def install_hints() -> List[str]:
    """Commands worth suggesting when no engine was found.

    Only engines you can actually install: telling a Windows user to run
    ``sudo apt install`` is worse than telling them nothing.
    """
    seen, hints = set(), []
    for engine_cls in preference_order():
        if engine_cls is TermuxEngine and not _under_termux():
            continue
        hint = engine_cls.install_hint()
        if hint and hint not in seen:
            seen.add(hint)
            hints.append(hint)
    return hints


def _under_termux() -> bool:
    import os

    return bool(os.environ.get("TERMUX_VERSION")) or os.environ.get(
        "PREFIX", ""
    ).startswith("/data/data/com.termux")
