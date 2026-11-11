"""Command lines and voice discovery for each speech engine."""

import platform
import shutil
import subprocess

import pytest

from jupyter_tts_alerts import engines
from jupyter_tts_alerts.engines import (
    EspeakEngine,
    NullEngine,
    SapiEngine,
    SayEngine,
    SpdSayEngine,
    TermuxEngine,
    detect_engine,
    load_engine,
    preference_order,
)

ALL = [EspeakEngine, SpdSayEngine, SayEngine, SapiEngine, TermuxEngine]


@pytest.mark.parametrize("engine_cls", ALL)
def test_every_engine_builds_a_runnable_command(engine_cls):
    command = engine_cls("/usr/bin/fake").speak_command("hello", None, 50, 0)
    assert command.argv and command.argv[0] == "/usr/bin/fake"
    assert all(isinstance(part, str) for part in command.argv)


@pytest.mark.parametrize("engine_cls", ALL)
def test_text_is_never_shell_interpolated(engine_cls):
    """The message must arrive as one argv element or on stdin, never inline."""
    nasty = "boom; rm -rf /  $(whoami) `id`"
    command = engine_cls("/usr/bin/fake").speak_command(nasty, None, 50, 0)
    out_of_band = (command.stdin or "") + " ".join((command.env or {}).values())
    if "rm -rf /" in out_of_band:
        # Carried on stdin or in the environment: it never touches argv at all.
        assert "rm -rf /" not in " ".join(command.argv)
    else:
        # Otherwise exactly one argv element holds it, and it is the last one.
        assert sum("rm -rf /" in part for part in command.argv) == 1
        assert "rm -rf /" in command.argv[-1]


@pytest.mark.parametrize("volume, expected", [(0, "0"), (50, "100"), (100, "200")])
def test_espeak_maps_volume_to_its_own_scale(volume, expected):
    argv = EspeakEngine("espeak-ng").speak_command("x", None, volume, 0).argv
    assert argv[argv.index("-a") + 1] == expected


@pytest.mark.parametrize("volume, expected", [(0, "-100"), (50, "0"), (100, "100")])
def test_spd_say_maps_volume_to_its_own_scale(volume, expected):
    argv = SpdSayEngine("spd-say").speak_command("x", None, volume, 0).argv
    assert argv[argv.index("-i") + 1] == expected


def test_espeak_rate_stays_inside_the_engine_limits():
    slow = EspeakEngine("espeak-ng").speak_command("x", None, 50, -100).argv
    fast = EspeakEngine("espeak-ng").speak_command("x", None, 50, 100).argv
    assert 80 <= int(slow[slow.index("-s") + 1]) <= 450
    assert 80 <= int(fast[fast.index("-s") + 1]) <= 450
    assert int(slow[slow.index("-s") + 1]) < int(fast[fast.index("-s") + 1])


def test_spd_say_lists_only_the_voice_types_it_can_actually_check():
    """`-L` emits ~15k unparseable rows; `-y` accepts nonsense and exits 0."""
    listed = SpdSayEngine("spd-say").voices()
    assert listed == list(SpdSayEngine.VOICE_TYPES)


def test_spd_say_picks_the_right_flag_for_the_kind_of_voice():
    symbolic = SpdSayEngine("spd-say").speak_command("x", "female1", 50, 0).argv
    concrete = SpdSayEngine("spd-say").speak_command("x", "Alicia", 50, 0).argv
    assert "-t" in symbolic and symbolic[symbolic.index("-t") + 1] == "female1"
    assert "-y" in concrete and concrete[concrete.index("-y") + 1] == "Alicia"


def test_say_carries_volume_inline_because_it_has_no_flag():
    command = SayEngine("say").speak_command("hello", "Alex", 25, 0)
    assert command.argv[-1] == "[[volm 0.50]] hello"
    assert command.argv[command.argv.index("-v") + 1] == "Alex"


def test_sapi_passes_everything_through_the_environment():
    """PowerShell 5.1 garbles non-ASCII stdin, so the text travels in env."""
    command = SapiEngine("powershell").speak_command("hello", "Zira", 25, 50)
    assert command.env == {
        "JTTS_TEXT": "hello",
        "JTTS_VOLUME": "50",
        "JTTS_RATE": "5",
        "JTTS_VOICE": "Zira",
    }
    assert command.stdin is None
    assert "hello" not in " ".join(command.argv)


def test_sapi_script_is_base64_so_no_quoting_layer_can_mangle_it():
    import base64

    command = SapiEngine("powershell").speak_command("hello", None, 50, 0)
    encoded = command.argv[command.argv.index("-EncodedCommand") + 1]
    script = base64.b64decode(encoded).decode("utf-16-le")
    assert "System.Speech" in script
    assert "hello" not in script


@pytest.mark.parametrize(
    "engine_cls, flag, expected",
    [
        (EspeakEngine, "-a", "100"),      # espeak's own default amplitude
        (SpdSayEngine, "-i", "0"),        # speech-dispatcher's own default
    ],
)
def test_volume_50_means_the_engine_default(engine_cls, flag, expected):
    """A 0-100 knob is only meaningful if the midpoint means the same thing."""
    argv = engine_cls("x").speak_command("t", None, 50, 0).argv
    assert argv[argv.index(flag) + 1] == expected


def test_volume_50_means_the_engine_default_for_say_and_sapi():
    assert SayEngine("say").speak_command("t", None, 50, 0).argv[-1].startswith(
        "[[volm 1.00]]"
    )
    assert SapiEngine("ps").speak_command("t", None, 50, 0).env["JTTS_VOLUME"] == "100"


@pytest.mark.parametrize("engine_cls", [EspeakEngine, SpdSayEngine])
def test_options_are_terminated_before_the_message(engine_cls):
    """`spd-say "-100 percent failure"` exits 1 with `invalid option`."""
    argv = engine_cls("x").speak_command("-100 percent failure", None, 50, 0).argv
    assert argv[-2] == "--"
    assert argv[-1] == "-100 percent failure"


def test_engines_without_a_terminator_cannot_be_confused_by_a_dash():
    """`say` and termux take no `--`, so neither may expose a bare message."""
    assert SayEngine("say").speak_command("-100 down", None, 50, 0).argv[-1].startswith("[[")
    assert TermuxEngine("t").speak_command("-100 down", None, 50, 0).stdin == "-100 down"


def test_termux_passes_text_on_stdin():
    command = TermuxEngine("termux-tts-speak").speak_command("hello", None, 50, 0)
    assert command.stdin == "hello"


def test_null_engine_is_always_available_and_silent():
    engine = NullEngine.find()
    assert engine is not None
    assert engine.speak_command("hello", None, 50, 0).argv == []


def test_espeak_parses_a_voice_listing(monkeypatch):
    listing = (
        "Pty Language Age/Gender VoiceName          File          Other Languages\n"
        " 5  en-gb          M  english              gmw/en\n"
        " 5  en-us          M  english-us           gmw/en-US\n"
    )
    engine = EspeakEngine("espeak-ng")
    monkeypatch.setattr(engine, "_probe", lambda args: listing)
    assert engine.voices() == ["en-gb", "en-us"]


def test_espeak_default_voices_ignore_a_language_only_listing(monkeypatch):
    """Regression: "en-gb+m3" and "en-gb+f3" sounded identical on espeak-ng 1.52."""
    listing = (
        "Pty Language Age/Gender VoiceName          File          Other Languages\n"
        " 2  en-gb          M  English_(Great_Britain) gmw/en\n"
    )
    engine = EspeakEngine("espeak-ng")
    monkeypatch.setattr(engine, "_probe", lambda args: listing)
    assert engine.default_voices() == ("en+m3", "en+f3")


def test_espeak_falls_back_when_it_cannot_list_voices(monkeypatch):
    engine = EspeakEngine("espeak-ng")
    monkeypatch.setattr(engine, "_probe", lambda args: "")
    normal, alert = engine.default_voices()
    assert (normal, alert) == ("en+m3", "en+f3")


@pytest.mark.skipif(shutil.which("espeak-ng") is None, reason="espeak-ng is not installed")
def test_the_default_espeak_voices_really_sound_different():
    """Renders to stdout, so nothing is played; only the audio is compared."""
    engine = EspeakEngine(shutil.which("espeak-ng"))
    rendered = []
    for voice in engine.default_voices():
        argv = engine.speak_command("Error in cell 8.", voice, 50, 0).argv
        argv.insert(1, "--stdout")
        rendered.append(subprocess.run(argv, capture_output=True, timeout=30).stdout)
    assert rendered[0] and rendered[1]
    assert rendered[0] != rendered[1]


def test_say_parses_names_containing_spaces(monkeypatch):
    listing = (
        "Alex                en_US    # Most people recognize me by my voice.\n"
        "Bad News            en_US    # The light you see at the end...\n"
        "Samantha            en_US    # Hello, my name is Samantha.\n"
    )
    engine = SayEngine("say")
    monkeypatch.setattr(engine, "_probe", lambda args: listing)
    assert engine.voices() == ["Alex", "Bad News", "Samantha"]
    assert engine.default_voices() == ("Alex", "Samantha")


def test_sapi_pairs_a_male_and_a_female_voice(monkeypatch):
    engine = SapiEngine("powershell")
    monkeypatch.setattr(
        engine,
        "_probe",
        lambda args: "Male\tMicrosoft David Desktop\nFemale\tMicrosoft Zira Desktop\n",
    )
    assert engine.default_voices() == ("Microsoft David Desktop", "Microsoft Zira Desktop")


def test_spd_say_does_not_cancel_other_peoples_speech():
    """`spd-say -C` is CANCEL ALL: it would cut off Orca mid-sentence."""
    assert SpdSayEngine("spd-say").cancel_command() is None


def test_say_cannot_be_talked_into_running_an_embedded_command():
    """`[[...]]` is a live command channel in macOS say, not literal text."""
    argv = SayEngine("say").speak_command("[[slnc 5000]] hello", None, 50, 0).argv
    assert argv[-1] == "[[volm 1.00]] [ [slnc 5000]] hello"


@pytest.mark.parametrize(
    "system, expected",
    [
        ("Linux", ["sudo apt install espeak-ng", "sudo apt install speech-dispatcher"]),
        ("Darwin", ["brew install espeak-ng"]),
        ("Windows",
         ["install espeak-ng from github.com/espeak-ng/espeak-ng/releases"]),
    ],
)
def test_install_hints_fit_the_platform(monkeypatch, system, expected):
    """"try: ships with Windows, or sudo apt install espeak-ng" helps nobody."""
    monkeypatch.setattr(engines.platform, "system", lambda: system)
    monkeypatch.delenv("TERMUX_VERSION", raising=False)
    monkeypatch.setenv("PREFIX", "/usr")
    hints = engines.install_hints()
    assert hints == expected
    assert not any("ships with" in hint for hint in hints)
    if system != "Linux":
        assert not any("apt" in hint for hint in hints)


@pytest.mark.parametrize("system", ["Linux", "Darwin", "Windows"])
def test_a_bundled_engine_that_is_missing_says_so_without_a_dangling_hint(
    monkeypatch, system
):
    monkeypatch.setattr(engines.platform, "system", lambda: system)
    monkeypatch.setattr(engines.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError) as raised:
        load_engine("say")
    assert not str(raised.value).rstrip().endswith("try:")
    assert "ships with its operating system" in str(raised.value)


def test_the_bracket_escape_survives_a_three_dimensional_array():
    """`[[[` would come back out of a naive replace as a live `[[`."""
    for text in ("shape [[[1 2] [3 4]]]", "tensor([[[0.1]]])", "a [[[[ b"):
        spoken = SayEngine("say").speak_command(text, None, 50, 0).argv[-1]
        body = spoken.split("]] ", 1)[1]
        assert "[[" not in body


def test_termux_is_only_suggested_on_termux(monkeypatch):
    monkeypatch.setattr(engines.platform, "system", lambda: "Linux")
    monkeypatch.setenv("TERMUX_VERSION", "0.118")
    assert "pkg install termux-api" in engines.install_hints()


def test_spd_say_offers_two_distinguishable_defaults():
    normal, alert = SpdSayEngine("spd-say").default_voices()
    assert normal != alert


def test_probe_survives_a_missing_binary():
    assert EspeakEngine("/definitely/not/here").voices() == []


def test_load_engine_rejects_an_unknown_name():
    with pytest.raises(ValueError, match="unknown engine"):
        load_engine("festival-9000")


def test_load_engine_explains_how_to_install_a_missing_one(monkeypatch):
    monkeypatch.setattr(engines.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="not installed"):
        load_engine("espeak-ng")


def test_detection_returns_none_when_nothing_is_installed(monkeypatch):
    monkeypatch.setattr(engines.shutil, "which", lambda name: None)
    assert detect_engine() is None


@pytest.mark.parametrize(
    "system, first",
    [("Darwin", SayEngine), ("Windows", SapiEngine), ("Linux", EspeakEngine)],
)
def test_preference_order_is_platform_appropriate(monkeypatch, system, first):
    monkeypatch.setattr(engines.platform, "system", lambda: system)
    assert preference_order()[0] is first


@pytest.mark.skipif(platform.system() != "Linux", reason="Linux engine ordering")
def test_linux_falls_back_to_speech_dispatcher():
    assert SpdSayEngine in preference_order()
