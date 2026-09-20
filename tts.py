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
VOICE_MODEL_SHA256 = "49cf48023861f9fd42e13a8632f068fee67d1ce244a6ee38f29595afbf0a6be4"
VOICE_CONFIG_SHA256 = "4704af2736022e910a3f32672480d5530dd39da5c2bcc079f315f604166ff0de"
TEST_SENTENCE = "Ik volg een cursus omdat ik zo snel mogelijk Nederlands wil leren."


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


def piper_paths(root: Path | None = None) -> PiperPaths:
    root = Path(root) if root is not None else default_piper_root()
    active = root / "current"
    executable = "python.exe" if os.name == "nt" else "python"
    scripts_dir = "Scripts" if os.name == "nt" else "bin"
    model_dir = active / "models"
    return PiperPaths(
        root=root,
        active=active,
        python=active / "runtime" / scripts_dir / executable,
        model=model_dir / f"{DEFAULT_VOICE}.onnx",
        model_config=model_dir / f"{DEFAULT_VOICE}.onnx.json",
        manifest=active / "manifest.json",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def piper_install_status(root: Path | None = None) -> PiperInstallStatus:
    paths = piper_paths(root)
    required = (paths.python, paths.model, paths.model_config, paths.manifest)
    if not all(path.is_file() for path in required):
        return PiperInstallStatus(False, "Not installed (about 260 MB)")
    try:
        manifest = json.loads(paths.manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return PiperInstallStatus(False, "Installation is incomplete; repair required")
    if manifest.get("piper_version") != PIPER_VERSION or manifest.get("voice") != DEFAULT_VOICE:
        return PiperInstallStatus(False, "Installation version mismatch; repair required")
    return PiperInstallStatus(True, f"Installed: {DEFAULT_VOICE}")


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


def install_piper(root: Path | None = None, python_executable: str | None = None) -> PiperInstallStatus:
    paths = piper_paths(root)
    paths.root.mkdir(parents=True, exist_ok=True)
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
        _run_checked(
            [
                str(runtime_python),
                "-m",
                "piper.download_voices",
                "--download-dir",
                str(models),
                DEFAULT_VOICE,
            ]
        )
        model = models / f"{DEFAULT_VOICE}.onnx"
        model_config = models / f"{DEFAULT_VOICE}.onnx.json"
        if _sha256(model) != VOICE_MODEL_SHA256 or _sha256(model_config) != VOICE_CONFIG_SHA256:
            raise TTSInstallError("Downloaded Piper voice failed checksum verification.")
        (staging / "manifest.json").write_text(
            json.dumps(
                {
                    "piper_version": PIPER_VERSION,
                    "voice": DEFAULT_VOICE,
                    "model_sha256": VOICE_MODEL_SHA256,
                    "config_sha256": VOICE_CONFIG_SHA256,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

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
    return piper_install_status(paths.root)


def remove_piper_install(root: Path | None = None) -> None:
    paths = piper_paths(root)
    if paths.active.exists():
        shutil.rmtree(paths.active)


def tts_enabled_for_decks(config: dict[str, Any], decks: Sequence[str]) -> bool:
    tts_config = config.get("tts", {})
    if not bool(tts_config.get("enabled")):
        return False
    selected = {str(deck).strip() for deck in decks if str(deck).strip()}
    enabled = {str(deck).strip() for deck in tts_config.get("enabled_decks", []) if str(deck).strip()}
    return bool(selected) and selected.issubset(enabled)


def tts_round_eligible(config: dict[str, Any], decks: Sequence[str], direction: str) -> bool:
    return direction == RECOGNITION_DIRECTION and tts_enabled_for_decks(config, decks)


class PiperService:
    def __init__(self, *, length_scale: float = 1.0, root: Path | None = None) -> None:
        self.paths = piper_paths(root)
        if not piper_install_status(self.paths.root).installed:
            raise TTSUnavailableError("Piper is not installed. Open Tools -> AllAI -> Settings to install it.")
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
