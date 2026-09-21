from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from session import DEFAULT_CONFIG, PRODUCTION_DIRECTION, RECOGNITION_DIRECTION, deep_merge_config
import tts
from tts import (
    VoiceSpec,
    configured_deck_voices,
    install_piper,
    piper_install_status,
    piper_paths,
    tts_enabled_for_decks,
    tts_round_eligible,
    tts_round_voice,
    voice_for_decks,
)


class TTSTests(unittest.TestCase):
    def test_default_config_keeps_tts_disabled(self) -> None:
        config = deep_merge_config(DEFAULT_CONFIG, {})
        self.assertFalse(config["tts"]["enabled"])
        self.assertEqual(config["tts"]["deck_voices"], {})

    def test_round_eligibility_requires_recognition_and_all_selected_decks(self) -> None:
        config = {
            "tts": {
                "enabled": True,
                "deck_voices": {
                    "Dutch": tts.DEFAULT_VOICE,
                    "Dutch::Grammar": tts.DEFAULT_VOICE,
                    "Mandarin": "zh_CN-huayan-medium",
                },
            }
        }
        self.assertTrue(tts_round_eligible(config, ["Dutch"], RECOGNITION_DIRECTION))
        self.assertTrue(
            tts_round_eligible(config, ["Dutch", "Dutch::Grammar"], RECOGNITION_DIRECTION)
        )
        self.assertTrue(tts_enabled_for_decks(config, ["Dutch"]))
        self.assertEqual(
            tts_round_voice(config, ["Mandarin"], RECOGNITION_DIRECTION),
            "zh_CN-huayan-medium",
        )
        self.assertFalse(tts_round_eligible(config, ["Dutch"], PRODUCTION_DIRECTION))
        self.assertIsNone(voice_for_decks(config, ["Dutch", "Mandarin"]))

    def test_legacy_enabled_decks_migrate_to_default_voice(self) -> None:
        config = {"tts": {"enabled": True, "enabled_decks": ["Dutch"]}}
        self.assertEqual(configured_deck_voices(config), {"Dutch": tts.DEFAULT_VOICE})
        self.assertEqual(voice_for_decks(config, ["Dutch"]), tts.DEFAULT_VOICE)

    def test_install_status_requires_complete_manifest_and_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertFalse(piper_install_status(root).installed)
            paths = piper_paths(root)
            for path in (paths.python, paths.model, paths.model_config):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"test")
            paths.manifest.write_text(
                json.dumps({"piper_version": tts.PIPER_VERSION, "voices": [tts.DEFAULT_VOICE]}),
                encoding="utf-8",
            )
            fake_spec = VoiceSpec(
                voice_id=tts.DEFAULT_VOICE,
                display_name="Test",
                language="test",
                test_sentence="test",
                model_sha256=hashlib.sha256(b"test").hexdigest(),
                config_sha256=hashlib.sha256(b"test").hexdigest(),
            )
            with patch.dict(tts.VOICE_SPECS, {tts.DEFAULT_VOICE: fake_spec}, clear=True):
                self.assertTrue(piper_install_status(root).installed)

    def test_installer_activates_only_after_checksum_verification(self) -> None:
        model_bytes = b"model"
        config_bytes = b"config"
        model_hash = hashlib.sha256(model_bytes).hexdigest()
        config_hash = hashlib.sha256(config_bytes).hexdigest()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "piper"

            def fake_run(command: list[str], *, timeout: int = 900) -> None:
                if command[1:3] == ["-m", "venv"]:
                    runtime = Path(command[3])
                    python = runtime / "bin" / "python"
                    python.parent.mkdir(parents=True)
                    python.write_bytes(b"python")
                elif "piper.download_voices" in command:
                    models = Path(command[command.index("--download-dir") + 1])
                    (models / f"{tts.DEFAULT_VOICE}.onnx").write_bytes(model_bytes)
                    (models / f"{tts.DEFAULT_VOICE}.onnx.json").write_bytes(config_bytes)

            fake_spec = VoiceSpec(
                voice_id=tts.DEFAULT_VOICE,
                display_name="Test",
                language="test",
                test_sentence="test",
                model_sha256=model_hash,
                config_sha256=config_hash,
            )
            with patch("tts._run_checked", side_effect=fake_run), patch.dict(
                tts.VOICE_SPECS, {tts.DEFAULT_VOICE: fake_spec}, clear=True
            ):
                status = install_piper(root, python_executable="python3")

            self.assertTrue(status.installed)
            self.assertEqual(piper_paths(root).model.read_bytes(), model_bytes)
            self.assertFalse(any(path.name.startswith("installing-") for path in root.iterdir()))

    def test_voice_paths_share_runtime_but_use_separate_models(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dutch = piper_paths(Path(tmp), tts.DEFAULT_VOICE)
            mandarin = piper_paths(Path(tmp), "zh_CN-huayan-medium")
            self.assertEqual(dutch.python, mandarin.python)
            self.assertNotEqual(dutch.model, mandarin.model)

    def test_failed_repair_preserves_previous_install(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "piper"
            active = root / "current"
            active.mkdir(parents=True)
            marker = active / "existing-install"
            marker.write_text("keep", encoding="utf-8")

            with patch("tts._run_checked", side_effect=tts.TTSInstallError("download failed")):
                with self.assertRaises(tts.TTSInstallError):
                    install_piper(root, python_executable="python3")

            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
            self.assertFalse(any(path.name.startswith("installing-") for path in root.iterdir()))


if __name__ == "__main__":
    unittest.main()
