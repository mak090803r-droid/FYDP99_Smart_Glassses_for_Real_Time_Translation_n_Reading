"""Private offline CPU ASR process; started only after an explicit PTT recording."""
import base64
import json
import os
import sys
import time
from pathlib import Path

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["OMP_NUM_THREADS"] = "2"


def main():
    import numpy as np
    from faster_whisper import WhisperModel
    model_path = Path(sys.argv[1])
    if not (model_path / "model.bin").is_file():
        raise RuntimeError("Local Whisper assets are missing; run setup_box7_voice.py explicitly.")
    model = WhisperModel(str(model_path), device="cpu", compute_type="int8",
                         cpu_threads=2, num_workers=1, local_files_only=True)
    for line in sys.stdin:
        try:
            request = json.loads(line)
            raw = base64.b64decode(request["pcm"], validate=True)
            if not 3200 <= len(raw) <= 960000 or len(raw) % 2:
                raise ValueError("Recording must be between 0.1 and 30 seconds, mono 16 kHz PCM16.")
            audio = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
            started = time.perf_counter()
            if float(np.sqrt(np.mean(audio ** 2))) < 0.002:
                result = {"text": "", "reason": "No speech detected", "seconds": 0.0}
            else:
                segments, _ = model.transcribe(audio, language="en", beam_size=3,
                    condition_on_previous_text=False, temperature=0.0, vad_filter=True,
                    initial_prompt=("Reading controls: skip paragraph one; repeat paragraph two; "
                                    "read from paragraph three; headings; title; continue reading. "
                                    "The user may also ask a question about the document."),
                    vad_parameters={"min_silence_duration_ms": 350},
                    no_speech_threshold=0.6, log_prob_threshold=-1.0,
                    hallucination_silence_threshold=1.0)
                segments = list(segments)
                accepted = [s.text.strip() for s in segments if s.avg_logprob >= -1.0 and s.no_speech_prob < 0.6]
                result = {"text": " ".join(accepted), "seconds": time.perf_counter() - started,
                          "segments": len(segments)}
            print(json.dumps(result, ensure_ascii=True), flush=True)
        except Exception as exc:
            print(json.dumps({"error": str(exc)}), flush=True)


if __name__ == "__main__":
    main()
