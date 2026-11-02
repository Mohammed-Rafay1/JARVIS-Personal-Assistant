"""Fetch the offline models Jarvis needs (~250 MB). Safe to re-run; skips what's already there."""
from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from jarvis.config import MODELS, load_config  # noqa: E402

KOKORO = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"


def fetch(url: str, dest: Path) -> None:
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  ok   {dest.name}")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    print(f"  get  {dest.name} ...", flush=True)

    def hook(blocks, bs, total):
        if total > 0 and blocks % 200 == 0:
            print(f"       {min(100, blocks * bs * 100 // total)}%", flush=True)

    urllib.request.urlretrieve(url, tmp, hook)
    tmp.replace(dest)


def main() -> None:
    cfg = load_config()
    print("Wake word (openWakeWord):")
    import openwakeword.utils

    openwakeword.utils.download_models([cfg["wake_word"]["model"]])
    print("  ok")

    print(f"Speech recognition (Whisper {cfg['stt']['model']}):")
    from faster_whisper import WhisperModel

    WhisperModel(cfg["stt"]["model"], device="cpu", compute_type="int8", download_root=str(MODELS / "whisper"))
    print("  ok")

    print("Voice (Piper, fast):")
    pv = cfg["tts"].get("piper_voice", "en_GB-alan-medium")
    lang, rest = pv.split("-", 1)
    speaker, quality = rest.rsplit("-", 1)
    base = f"https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/{lang.split('_')[0]}/{lang}/{speaker}/{quality}/"
    fetch(base + pv + ".onnx", MODELS / "piper" / f"{pv}.onnx")
    fetch(base + pv + ".onnx.json", MODELS / "piper" / f"{pv}.onnx.json")
    print("Voice (Kokoro, natural):")
    fetch(KOKORO + "kokoro-v1.0.int8.onnx", MODELS / "kokoro" / "kokoro-v1.0.int8.onnx")
    fetch(KOKORO + "voices-v1.0.bin", MODELS / "kokoro" / "voices-v1.0.bin")
    print("Done.")


if __name__ == "__main__":
    main()
