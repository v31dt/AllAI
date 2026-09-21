from __future__ import annotations

import hashlib
import json
import os
import select
import shutil
import subprocess
import tempfile
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

try:  # pragma: no cover - import mode depends on Anki loader vs local tests
    from .session import RECOGNITION_DIRECTION
except ImportError:  # pragma: no cover
    from session import RECOGNITION_DIRECTION

PIPER_VERSION = "1.8.0"
PIPER_PACKAGES = (
    "flatbuffers==25.12.19",
    "numpy==2.5.3",
    "onnxruntime==1.30.0",
    "packaging==26.3",
    "pathvalidate==3.3.1",
    "piper-tts==1.8.0",
    "protobuf==7.36.2",
)
DEFAULT_VOICE = "nl_BE-nathalie-medium"


@dataclass(frozen=True)
class VoiceSpec:
    voice_id: str
    display_name: str
    language: str
    test_sentence: str
    model_sha256: str
    config_sha256: str


VOICE_SPECS = {
    DEFAULT_VOICE: VoiceSpec(
        voice_id=DEFAULT_VOICE,
        display_name="Dutch (Belgium) - Nathalie",
        language="nl_BE",
        test_sentence="Ik volg een cursus omdat ik zo snel mogelijk Nederlands wil leren.",
        model_sha256="49cf48023861f9fd42e13a8632f068fee67d1ce244a6ee38f29595afbf0a6be4",
        config_sha256="4704af2736022e910a3f32672480d5530dd39da5c2bcc079f315f604166ff0de",
    ),
    "zh_CN-huayan-medium": VoiceSpec(
        voice_id="zh_CN-huayan-medium",
        display_name="Mandarin Chinese - Huayan",
        language="zh_CN",
        test_sentence="我的朋友最喜欢吃面条、饺子和包子。",
        model_sha256="9929917bf8cabb26fd528ea44d3a6699c11e87317a14765312420be230be0f3d",
        config_sha256="d521dc45504a8ccc99e325822b35946dd701840bfb07e3dbb31a40929ed6a82b",
    ),
}

class TTSInstallError(Exception):
    pass


class TTSUnavailableError(Exception):
    pass


@dataclass(frozen=True)
class PiperPaths:
    root: Path
    active: Path
    python: Path
    model: Path
    model_config: Path
    manifest: Path


@dataclass(frozen=True)
class PiperInstallStatus:
    installed: bool
    detail: str


@dataclass(frozen=True)
class AudioResult:
    request_id: int
    path: Path
    duration_ms: int


def default_piper_root() -> Path:
    return Path(__file__).resolve().parent / "user_files" / "piper"


def piper_paths(root: Path | None = None, voice_id: str = DEFAULT_VOICE) -> PiperPaths:
    root = Path(root) if root is not None else default_piper_root()
    active = root / "current"
    executable = "python.exe" if os.name == "nt" else "python"
    scripts_dir = "Scripts" if os.name == "nt" else "bin"
    model_dir = active / "models"
    return PiperPaths(
        root=root,
        active=active,
        python=active / "runtime" / scripts_dir / executable,
        model=model_dir / f"{voice_id}.onnx",
        model_config=model_dir / f"{voice_id}.onnx.json",
        manifest=active / "manifest.json",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def piper_install_status(
    root: Path | None = None, voice_id: str = DEFAULT_VOICE
) -> PiperInstallStatus:
    if voice_id not in VOICE_SPECS:
        return PiperInstallStatus(False, f"Unknown voice: {voice_id}")
    paths = piper_paths(root, voice_id)
    if not paths.python.is_file():
        return PiperInstallStatus(False, "Piper runtime not installed")
    if not paths.model.is_file() or not paths.model_config.is_file():
        return PiperInstallStatus(False, f"Voice not installed: {voice_id}")
    spec = VOICE_SPECS[voice_id]
    if not paths.manifest.is_file():
        return PiperInstallStatus(False, "Installation is incomplete; repair required")
    try:
        manifest = json.loads(paths.manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return PiperInstallStatus(False, "Installation is incomplete; repair required")
    if manifest.get("piper_version") != PIPER_VERSION:
        return PiperInstallStatus(False, "Installation version mismatch; repair required")
    return PiperInstallStatus(True, f"Installed: {spec.display_name}")


def _run_checked(command: Sequence[str], *, timeout: int = 900) -> None:
    try:
        completed = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TTSInstallError(f"Could not run {' '.join(command[:3])}: {exc}") from exc
    if completed.returncode != 0:
        output = (completed.stderr or completed.stdout or "No error output").strip()
        raise TTSInstallError(output[-2000:])


def _verify_downloaded_voice(models: Path, voice_id: str) -> None:
    spec = VOICE_SPECS[voice_id]
    model = models / f"{voice_id}.onnx"
    model_config = models / f"{voice_id}.onnx.json"
    if not model.is_file() or not model_config.is_file():
        raise TTSInstallError(f"Piper did not download all files for {voice_id}.")
    if _sha256(model) != spec.model_sha256 or _sha256(model_config) != spec.config_sha256:
        raise TTSInstallError(f"Downloaded Piper voice failed checksum verification: {voice_id}")


def _installed_voice_verified(paths: PiperPaths, voice_id: str) -> bool:
    spec = VOICE_SPECS[voice_id]
    return (
        paths.model.is_file()
        and paths.model_config.is_file()
        and _sha256(paths.model) == spec.model_sha256
        and _sha256(paths.model_config) == spec.config_sha256
    )


def _write_manifest(active: Path, voice_ids: Sequence[str]) -> None:
    installed = sorted(
        voice_id
        for voice_id in VOICE_SPECS
        if (active / "models" / f"{voice_id}.onnx").is_file()
        and (active / "models" / f"{voice_id}.onnx.json").is_file()
    )
    installed = sorted(set(installed) | set(voice_ids))
    (active / "manifest.json").write_text(
        json.dumps(
            {
                "piper_version": PIPER_VERSION,
                "voices": installed,
                "voice": DEFAULT_VOICE if DEFAULT_VOICE in installed else (installed[0] if installed else ""),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _install_voices_into_existing_runtime(paths: PiperPaths, voice_ids: Sequence[str]) -> None:
    models = paths.active / "models"
    models.mkdir(parents=True, exist_ok=True)
    for voice_id in voice_ids:
        voice_paths = piper_paths(paths.root, voice_id)
        if _installed_voice_verified(voice_paths, voice_id):
            continue
        staging = paths.root / f"voice-{uuid.uuid4().hex}"
        try:
            staging.mkdir(parents=True)
            _run_checked(
                [
                    str(paths.python),
                    "-m",
                    "piper.download_voices",
                    "--download-dir",
                    str(staging),
                    voice_id,
                ]
            )
            _verify_downloaded_voice(staging, voice_id)
            os.replace(staging / f"{voice_id}.onnx", models / f"{voice_id}.onnx")
            os.replace(staging / f"{voice_id}.onnx.json", models / f"{voice_id}.onnx.json")
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    _write_manifest(paths.active, voice_ids)


def install_piper(
    root: Path | None = None,
    python_executable: str | None = None,
    voice_ids: Sequence[str] | None = None,
) -> PiperInstallStatus:
    requested = list(dict.fromkeys(voice_ids or [DEFAULT_VOICE]))
    unknown = [voice_id for voice_id in requested if voice_id not in VOICE_SPECS]
    if unknown:
        raise TTSInstallError(f"Unknown Piper voice: {unknown[0]}")
    paths = piper_paths(root, requested[0])
    paths.root.mkdir(parents=True, exist_ok=True)
    if paths.python.is_file():
        _install_voices_into_existing_runtime(paths, requested)
        return piper_install_status(paths.root, requested[0])

    staging = paths.root / f"installing-{uuid.uuid4().hex}"
    backup = paths.root / f"backup-{uuid.uuid4().hex}"
    source_python = python_executable or shutil.which("python3")
    if not source_python:
        raise TTSInstallError("Python 3 was not found; it is required to install local speech.")

    try:
        runtime = staging / "runtime"
        models = staging / "models"
        models.mkdir(parents=True)
        _run_checked([source_python, "-m", "venv", str(runtime)])
        executable = "python.exe" if os.name == "nt" else "python"
        scripts_dir = "Scripts" if os.name == "nt" else "bin"
        runtime_python = runtime / scripts_dir / executable
        _run_checked(
            [
                str(runtime_python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-cache-dir",
                *PIPER_PACKAGES,
            ]
        )
        for voice_id in requested:
            _run_checked(
                [
                    str(runtime_python),
                    "-m",
                    "piper.download_voices",
                    "--download-dir",
                    str(models),
                    voice_id,
                ]
            )
            _verify_downloaded_voice(models, voice_id)
        _write_manifest(staging, requested)

        if paths.active.exists():
            paths.active.rename(backup)
        staging.rename(paths.active)
        if backup.exists():
            shutil.rmtree(backup)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        if backup.exists() and not paths.active.exists():
            backup.rename(paths.active)
        raise
    return piper_install_status(paths.root, requested[0])


def remove_piper_voice(voice_id: str, root: Path | None = None) -> None:
    paths = piper_paths(root, voice_id)
    paths.model.unlink(missing_ok=True)
    paths.model_config.unlink(missing_ok=True)
    if paths.active.exists():
        _write_manifest(paths.active, [])


def remove_piper_install(root: Path | None = None) -> None:
    paths = piper_paths(root)
    if paths.active.exists():
        shutil.rmtree(paths.active)


def configured_deck_voices(config: dict[str, Any]) -> dict[str, str]:
    tts_config = config.get("tts", {})
    # Upgrade the original Dutch-only deck checklist without requiring users to
    # reconfigure it after installing multi-language support. Explicit mappings
    # win when both formats are temporarily present.
    configured = {
        str(deck).strip(): DEFAULT_VOICE
        for deck in tts_config.get("enabled_decks", [])
        if str(deck).strip()
    }
    configured.update(
        {
            str(deck).strip(): str(voice_id).strip()
            for deck, voice_id in (tts_config.get("deck_voices", {}) or {}).items()
            if str(deck).strip() and str(voice_id).strip() in VOICE_SPECS
        }
    )
    return configured


def voice_for_decks(config: dict[str, Any], decks: Sequence[str]) -> str | None:
    if not bool(config.get("tts", {}).get("enabled")):
        return None
    selected = {str(deck).strip() for deck in decks if str(deck).strip()}
    assignments = configured_deck_voices(config)
    voices = {assignments.get(deck) for deck in selected}
    if not selected or None in voices or len(voices) != 1:
        return None
    return next(iter(voices))


def tts_enabled_for_decks(config: dict[str, Any], decks: Sequence[str]) -> bool:
    return voice_for_decks(config, decks) is not None


def tts_round_voice(config: dict[str, Any], decks: Sequence[str], direction: str) -> str | None:
    if direction != RECOGNITION_DIRECTION:
        return None
    return voice_for_decks(config, decks)


def tts_round_eligible(config: dict[str, Any], decks: Sequence[str], direction: str) -> bool:
    return tts_round_voice(config, decks, direction) is not None


class PiperService:
    def __init__(
        self,
        *,
        voice_id: str = DEFAULT_VOICE,
        length_scale: float = 1.0,
        root: Path | None = None,
    ) -> None:
        self.voice_id = voice_id
        self.paths = piper_paths(root, voice_id)
        if not piper_install_status(self.paths.root, voice_id).installed:
            raise TTSUnavailableError(
                f"Piper voice {voice_id} is not installed. Open Tools -> AllAI -> Settings to install it."
            )
        self.length_scale = max(0.5, min(2.0, float(length_scale)))
        self._worker_script = Path(__file__).resolve().with_name("tts_worker.py")
        self._process: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._temp_dir = tempfile.TemporaryDirectory(prefix="allai-tts-")

    @staticmethod
    def _read_worker_line(process: subprocess.Popen[str], timeout: float = 30) -> str:
        assert process.stdout is not None
        readable, _, _ = select.select([process.stdout], [], [], timeout)
        if not readable:
            process.kill()
            raise TTSUnavailableError("Piper did not respond in time.")
        line = process.stdout.readline()
        if not line:
            raise TTSUnavailableError("Piper stopped without returning audio.")
        return line

    def _start_locked(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        try:
            self._process = subprocess.Popen(
                [
                    str(self.paths.python),
                    str(self._worker_script),
                    str(self.paths.model),
                    str(self.paths.model_config),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            raise TTSUnavailableError(f"Could not start Piper: {exc}") from exc
        ready_line = self._read_worker_line(self._process)
        try:
            ready = json.loads(ready_line)
        except json.JSONDecodeError as exc:
            raise TTSUnavailableError("Piper worker failed to start.") from exc
        if not ready.get("ready"):
            raise TTSUnavailableError(str(ready.get("error") or "Piper worker failed to start."))

    def synthesize(self, text: str, request_id: int) -> AudioResult:
        with self._lock:
            self._start_locked()
            assert self._process is not None
            assert self._process.stdin is not None
            assert self._process.stdout is not None
            output_path = Path(self._temp_dir.name) / f"round-{request_id}.wav"
            request = {
                "id": int(request_id),
                "text": str(text),
                "output_path": str(output_path),
                "length_scale": self.length_scale,
            }
            try:
                self._process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
                self._process.stdin.flush()
                response_line = self._read_worker_line(self._process)
                response = json.loads(response_line)
            except (BrokenPipeError, OSError, json.JSONDecodeError) as exc:
                raise TTSUnavailableError("Piper stopped while generating audio.") from exc
            if not response.get("ok"):
                raise TTSUnavailableError(str(response.get("error") or "Piper could not generate audio."))
            if not output_path.is_file():
                raise TTSUnavailableError("Piper reported success without producing audio.")
            return AudioResult(
                request_id=int(response.get("id", request_id)),
                path=output_path,
                duration_ms=int(response.get("duration_ms", 0)),
            )

    def close(self) -> None:
        with self._lock:
            process = self._process
            self._process = None
            if process is not None and process.poll() is None:
                try:
                    assert process.stdin is not None
                    process.stdin.write('{"command":"shutdown"}\n')
                    process.stdin.flush()
                    process.wait(timeout=2)
                except Exception:
                    process.kill()
                    try:
                        process.wait(timeout=2)
                    except Exception:
                        pass
            self._temp_dir.cleanup()
