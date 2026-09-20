from __future__ import annotations

import json
import sys
import time
import wave
from pathlib import Path


def emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def main() -> int:
    if len(sys.argv) != 3:
        emit({"ready": False, "error": "Expected model and config paths."})
        return 2

    try:
        from piper import PiperVoice, SynthesisConfig

        voice = PiperVoice.load(Path(sys.argv[1]), config_path=Path(sys.argv[2]))
    except Exception as exc:
        emit({"ready": False, "error": str(exc)})
        return 1

    emit({"ready": True})
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if request.get("command") == "shutdown":
                return 0
            started = time.monotonic()
            output_path = Path(request["output_path"])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            synthesis_config = SynthesisConfig(length_scale=float(request.get("length_scale", 1.0)))
            with wave.open(str(output_path), "wb") as wav_file:
                voice.synthesize_wav(str(request["text"]), wav_file, syn_config=synthesis_config)
            emit(
                {
                    "id": int(request["id"]),
                    "ok": True,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                }
            )
        except Exception as exc:
            emit({"id": request.get("id", -1) if "request" in locals() else -1, "ok": False, "error": str(exc)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
