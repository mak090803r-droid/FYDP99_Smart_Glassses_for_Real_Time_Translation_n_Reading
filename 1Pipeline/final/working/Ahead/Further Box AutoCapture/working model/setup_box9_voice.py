"""Explicit one-time model provisioning; never called by the running pipeline.

Run with .venv_box7_voice/Scripts/python.exe after installing faster-whisper.
This downloads the English medium model. Runtime transcription is fully local.
"""
import os
from pathlib import Path


def main():
    os.environ.pop("HF_HUB_OFFLINE", None)
    os.environ.pop("TRANSFORMERS_OFFLINE", None)
    from huggingface_hub import snapshot_download
    destination = Path(__file__).parent / "box9_models" / "faster-whisper-medium.en"
    print(f"Downloading Systran/faster-whisper-medium.en into {destination}", flush=True)
    snapshot_download("Systran/faster-whisper-medium.en", local_dir=str(destination),
                      allow_patterns=["model.bin", "config.json", "tokenizer.json", "vocabulary.*", "preprocessor_config.json"])
    print("Local voice assets ready. Runtime will not download models.", flush=True)


if __name__ == "__main__":
    main()
