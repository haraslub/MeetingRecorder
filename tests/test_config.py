import json

import pytest

from audiorecorder import config


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """Point the module at a throwaway directory so tests never touch the real settings."""
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "AudioRecorder")
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "AudioRecorder" / "config.json")
    return config.CONFIG_PATH


class TestLoadConfig:
    def test_missing_file_gives_the_defaults(self, isolated_config):
        assert config.load_config() == config.DEFAULTS

    def test_defaults_are_not_shared_between_calls(self, isolated_config):
        first = config.load_config()
        first["output_dir"] = "changed"
        assert config.load_config()["output_dir"] == config.DEFAULTS["output_dir"]

    def test_stored_values_win_over_defaults(self, isolated_config):
        isolated_config.parent.mkdir(parents=True)
        isolated_config.write_text(json.dumps({"language": "cs"}), encoding="utf-8")
        assert config.load_config()["language"] == "cs"

    def test_keys_added_since_the_file_was_written_get_their_default(self, isolated_config):
        isolated_config.parent.mkdir(parents=True)
        isolated_config.write_text(json.dumps({"language": "cs"}), encoding="utf-8")
        loaded = config.load_config()
        assert loaded["summary_prompt"] == config.DEFAULT_SUMMARY_PROMPT
        assert loaded["system_source_name"] is None
        assert loaded["slide_snapshots"] is False


class TestSaveConfig:
    def test_creates_the_directory(self, isolated_config):
        config.save_config({"language": "cs"})
        assert isolated_config.exists()

    def test_round_trip(self, isolated_config):
        config.save_config({**config.DEFAULTS, "language": "sk", "mic_volume": 0.5})
        loaded = config.load_config()
        assert loaded["language"] == "sk"
        assert loaded["mic_volume"] == 0.5

    def test_non_ascii_is_written_readably(self, isolated_config):
        config.save_config({**config.DEFAULTS, "output_dir": "C:/Nahrávky"})
        assert "Nahrávky" in isolated_config.read_text(encoding="utf-8")


class TestDefaults:
    def test_public_default_is_english_without_translation(self):
        assert config.DEFAULTS["language"] == "en"
        assert config.DEFAULTS["enable_translation"] is False

    def test_summary_prompt_carries_both_placeholders(self):
        formatted = config.DEFAULT_SUMMARY_PROMPT.format(
            transcript_name="a.md", summary_name="a.summary.md"
        )
        assert "a.md" in formatted
        assert "a.summary.md" in formatted

    def test_devices_are_unset_by_default(self):
        assert config.DEFAULTS["system_source_name"] is None
        assert config.DEFAULTS["mic_source_name"] is None

    def test_live_subtitles_are_off_by_default(self):
        """They bill realtime minutes for a whole call, so nobody gets them by accident."""
        assert config.DEFAULTS["rts_translate"] is False

    def test_live_speakers_are_off_by_default(self):
        assert config.DEFAULTS["live_speakers"] is False

    def test_meeting_detection_is_opt_in(self):
        assert config.DEFAULTS["meeting_detection"] is False

    def test_calendar_detection_requires_an_explicit_opt_in(self):
        assert config.DEFAULTS["calendar_detection"] is False

    def test_automatic_recording_requires_an_explicit_opt_in(self):
        assert config.DEFAULTS["automatic_recording"] is False

    def test_launch_at_login_requires_an_explicit_opt_in(self):
        assert config.DEFAULTS["launch_at_login"] is False

    def test_the_subtitle_toggle_survives_a_restart(self, isolated_config):
        config.save_config({**config.DEFAULTS, "rts_translate": True})
        assert config.load_config()["rts_translate"] is True

    def test_the_speaker_toggle_survives_a_restart(self, isolated_config):
        config.save_config({**config.DEFAULTS, "live_speakers": True})
        assert config.load_config()["live_speakers"] is True


@pytest.mark.parametrize("stored", ["{broken", "[]", "null"])
def test_corrupt_config_is_preserved_and_automation_stays_off(isolated_config, stored):
    isolated_config.parent.mkdir(parents=True)
    isolated_config.write_text(stored)
    cfg = config.load_config()
    assert not cfg["automatic_recording"] and not cfg["auto_stop_recording"]
    assert isolated_config.read_text() == stored
    assert next(isolated_config.parent.glob("config.invalid-*.json")).read_text() == stored


@pytest.mark.parametrize("value", [True, False, "5", -1, 0, 121, None, 5.5])
def test_invalid_delay_never_becomes_an_immediate_command(value):
    cfg = config.validated_config({"auto_start_delay_seconds": value, "automatic_recording": "yes"})
    assert cfg["auto_start_delay_seconds"] == 5
    assert not cfg["automatic_recording"]


def test_atomic_save_failure_preserves_previous_file(isolated_config, monkeypatch):
    config.save_config({"language": "cs"})
    before = isolated_config.read_bytes()
    monkeypatch.setattr(config.os, "replace", lambda *args: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError):
        config.save_config({"language": "en"})
    assert isolated_config.read_bytes() == before
    assert not list(isolated_config.parent.glob(".config-*"))


def test_corrupt_capture_evidence_cannot_break_settings_or_qualify_automation():
    for value in (
        {},
        {"config": {}, "frames": {}, "time": None},
        {"config": {}, "frames": {}, "time": float("nan")},
    ):
        assert config.validated_config({"capture_readiness": value})["capture_readiness"] is None


def test_qualification_dictionaries_are_not_shared_defaults():
    first = config.validated_config({})
    first["native_call_qualifications"]["app"] = "x"
    assert not config.validated_config({})["native_call_qualifications"]
    assert not config.DEFAULTS["native_call_qualifications"]


def test_the_default_output_folder_is_named_after_the_product():
    assert config.DEFAULTS["output_dir"].endswith("MeetingRecorder")


class TestLegacyOutputFolder:
    def settings(self, output_dir, tested=None):
        readiness = None if tested is None else {"config": {"output_dir": str(tested)}}
        return {"output_dir": str(output_dir), "capture_readiness": readiness}

    def test_the_old_default_folder_moves_with_its_recordings(self, tmp_path):
        legacy, current = tmp_path / "AudioRecordings", tmp_path / "MeetingRecorder"
        (legacy / "2026-11-04 10-00-00").mkdir(parents=True)
        cfg = self.settings(legacy, tested=legacy)

        assert config.move_legacy_output_dir(cfg, legacy, current)

        assert cfg["output_dir"] == str(current)
        assert cfg["capture_readiness"]["config"]["output_dir"] == str(current)
        assert not legacy.exists()
        assert (current / "2026-11-04 10-00-00").is_dir()

    def test_a_folder_the_user_chose_stays(self, tmp_path):
        legacy, chosen = tmp_path / "AudioRecordings", tmp_path / "Calls"
        legacy.mkdir()
        cfg = self.settings(chosen)

        assert not config.move_legacy_output_dir(cfg, legacy, tmp_path / "MeetingRecorder")

        assert cfg["output_dir"] == str(chosen)
        assert legacy.exists()

    def test_an_existing_new_folder_is_never_merged(self, tmp_path):
        legacy, current = tmp_path / "AudioRecordings", tmp_path / "MeetingRecorder"
        legacy.mkdir()
        current.mkdir()
        cfg = self.settings(legacy)

        assert not config.move_legacy_output_dir(cfg, legacy, current)

        assert cfg["output_dir"] == str(legacy)
        assert legacy.exists()

    def test_a_missing_old_folder_switches_to_the_new_default(self, tmp_path):
        legacy, current = tmp_path / "AudioRecordings", tmp_path / "MeetingRecorder"
        cfg = self.settings(legacy)

        assert config.move_legacy_output_dir(cfg, legacy, current)

        assert cfg["output_dir"] == str(current)
