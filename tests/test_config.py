"""Settings: defaults, validation, and environment overrides."""

import pytest

from jupyter_tts_alerts.config import (
    Config,
    apply_environment,
    configure,
    env_flag,
    get_config,
)


def test_defaults_are_the_documented_ones():
    fresh = Config()
    assert fresh.enabled is True
    assert fresh.min_seconds == 5.0
    assert fresh.volume == 50
    assert fresh.notify is False
    assert fresh.phrase("cell") == "Cell {cell}. {duration}."


def test_configure_coerces_strings():
    config = configure(min_seconds="60", volume="80", notify="yes")
    assert config.min_seconds == 60.0
    assert config.volume == 80
    assert config.notify is True


def test_configure_rejects_unknown_settings():
    with pytest.raises(TypeError, match="unknown setting"):
        configure(nonsense=1)


def test_configure_suggests_the_near_miss():
    with pytest.raises(TypeError, match="did you mean 'min_seconds'"):
        configure(min_second=1)


@pytest.mark.parametrize(
    "kwargs", [{"volume": 500}, {"volume": -1}, {"rate": 900}, {"min_seconds": -1}]
)
def test_configure_rejects_out_of_range(kwargs):
    with pytest.raises(ValueError):
        configure(**kwargs)


def test_rejected_value_does_not_stick():
    with pytest.raises(ValueError):
        configure(volume=300)
    assert get_config().volume == 50          # the live config, not a fresh one


def test_a_rejected_value_does_not_wedge_every_later_call():
    """One bad number used to make configure() raise for the whole session."""
    with pytest.raises(ValueError):
        configure(volume=300)
    configure(min_seconds=60)                 # must not raise
    assert get_config().min_seconds == 60.0
    assert get_config().volume == 50


def test_a_multi_setting_call_is_all_or_nothing():
    with pytest.raises(ValueError):
        configure(volume=80, min_seconds=-5)
    assert get_config().volume == 50          # the good half was rolled back
    assert get_config().min_seconds == 5.0


def test_a_rejected_phrase_leaves_the_templates_alone():
    with pytest.raises(TypeError):
        configure(phrases={"cell": 123})
    assert get_config().phrase("cell") == "Cell {cell}. {duration}."


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_min_seconds_must_be_a_real_number(value):
    """NaN fails every comparison, so no cell would ever clear the threshold."""
    with pytest.raises(ValueError, match="real number"):
        configure(min_seconds=value)


def test_phrases_merge_rather_than_replace():
    configure(phrases={"cell": "Done: {duration}."})
    config = get_config()
    assert config.phrase("cell") == "Done: {duration}."
    assert config.phrase("error") == "Error in cell {cell}. {error_type}."


def test_unknown_phrase_key_is_rejected():
    with pytest.raises(ValueError, match="unknown phrase"):
        configure(phrases={"finished": "..."})


def test_voice_can_be_cleared():
    configure(voice="en+f2")
    assert get_config().voice == "en+f2"
    configure(voice=None)
    assert get_config().voice is None


def test_environment_overrides_are_applied():
    apply_environment({"JUPYTER_TTS_MIN_SECONDS": "45", "JUPYTER_TTS_ENABLED": "0"})
    config = get_config()
    assert config.min_seconds == 45.0
    assert config.enabled is False


def test_bad_environment_value_warns_but_does_not_raise():
    with pytest.warns(RuntimeWarning, match="ignoring JUPYTER_TTS_VOLUME"):
        apply_environment({"JUPYTER_TTS_VOLUME": "very loud"})
    assert get_config().volume == 50


def test_a_bad_variable_does_not_make_the_next_one_look_bad_too():
    """Each complaint must name the variable it is actually about."""
    with pytest.warns(RuntimeWarning) as warnings:
        apply_environment(
            {"JUPYTER_TTS_VOLUME": "500", "JUPYTER_TTS_NOTIFY": "1"}
        )
    messages = [str(w.message) for w in warnings]
    assert len(messages) == 1
    assert "JUPYTER_TTS_VOLUME" in messages[0]
    assert get_config().volume == 50          # rejected, so genuinely ignored
    assert get_config().notify is True        # and the valid one still applied


def test_the_one_setting_with_no_environment_variable_says_so():
    with pytest.warns(RuntimeWarning, match="PHRASES is not read"):
        apply_environment({"JUPYTER_TTS_PHRASES": '{"cell": "hi"}'})


def test_env_flag_reads_the_prefix(monkeypatch):
    monkeypatch.setenv("JUPYTER_TTS_AUTOSTART", "0")
    assert env_flag("AUTOSTART", True) is False
    monkeypatch.delenv("JUPYTER_TTS_AUTOSTART")
    assert env_flag("AUTOSTART", True) is True
