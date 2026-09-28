"""Repeatable unit + saved-image + local-ASR validation, isolated outputs.

No microphone recording, camera access, Pi deployment or audible playback.
Run with the existing fydp Python. All generated evidence stays in this folder.
"""
import argparse
import contextlib
import hashlib
import importlib
import json
from pathlib import Path
import socket
import statistics
import sys
import threading
import time
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(OUT), str(ROOT / "box6_validation")]


def units():
    import pipeline_cli_box7 as candidate
    import test_host, test_translation, test_audio_transport, test_box7
    # One historical test references a deleted log. Use the surviving archived
    # OCR for the same document, in memory; do not recreate/alter user logs.
    missing = ROOT / "paragraph_test_outputs/run_001_20260903_134627_ocr.txt"
    archived = ROOT / "box6_validation/end_to_end_outputs/capture_cli_001_20260908_143651_box6.json"
    content = json.loads(archived.read_text(encoding="utf-8"))
    equivalent = "\n".join("  SOURCE : " + r["source_text"] for r in content["regions"])
    original = Path.read_text
    def read(path, *args, **kwargs):
        return equivalent if path == missing and not path.exists() else original(path, *args, **kwargs)
    reports = []
    with (OUT / "unit_checks.log").open("w", encoding="utf-8") as log, \
         contextlib.redirect_stdout(log), contextlib.redirect_stderr(log), patch.object(Path, "read_text", read):
        for name, modules in (("box6_existing_suite", [test_host, test_translation, test_audio_transport]),
                              ("box7_voice_suite", [test_box7]),
                              ("box7_existing_host_behaviors", [test_host])):
            if name == "box7_existing_host_behaviors":
                test_host.host = candidate
            suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromModule(m) for m in modules)
            result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
            reports.append(dict(name=name, tests=result.testsRun, failures=len(result.failures),
                                errors=len(result.errors), skipped=len(result.skipped)))
    report = {"suites": reports, "fixture_substitution": {"missing": str(missing), "used": str(archived),
        "note": "Original suite initially failed only because its old OCR log is absent. Equivalent saved OCR was injected in memory for this one case."}}
    (OUT / "unit_checks.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)
    if any(r["failures"] or r["errors"] for r in reports):
        raise SystemExit("Unit regression failure; see unit_checks.log")


class SilentSink:
    def __init__(self): self.records = []
    def play(self, pcm, sample_rate, state):
        import numpy as np
        assert pcm.size and np.isfinite(pcm).all()
        self.records.append({"samples": len(pcm), "rate": sample_rate,
            "sha256": hashlib.sha256(pcm.tobytes()).hexdigest()})


def models():
    import cv2
    import numpy as np
    import pipeline_cli_box6 as old
    import pipeline_cli_box7 as new
    from box6_audio import AudioRouter, RoutedTTS
    from box6_capture import CaptureDetector
    from box6_runtime import ControlQueue
    from box7_voice import WhisperProcess, VoiceService
    from box7_runtime import PlaybackControls
    from box7_commands import parse_command
    class TracedTTS(RoutedTTS):
        def __init__(self, *args):
            self.spoken_requests = []
            super().__init__(*args)
        def speak(self, text):
            self.spoken_requests.append(text)
            return super().speak(text)
    destination = OUT / "model_outputs"
    destination.mkdir(exist_ok=True)
    report = {"camera_tested": False, "microphone_tested": False, "physical_audio_tested": False,
              "asr": [], "ocr": [], "capture": [], "pipeline": []}
    images = [ROOT / "box6_validation/end_to_end_outputs/captures/capture_cli_001_20260908_143651.jpg",
              ROOT / "paragraph_test_outputs/live_debug_sessions/session_20260909_085902/raw_analysis_frames/analysis_00075.jpg"]
    process = WhisperProcess(ROOT)
    router = voice_service = None
    try:
        with (OUT / "model_checks.log").open("w", encoding="utf-8") as log, \
             contextlib.redirect_stdout(log), contextlib.redirect_stderr(log), \
             patch.object(socket.socket, "connect", side_effect=RuntimeError("External network disabled in validation")):
            old._ensure_box5_tts_import_path()
            # Synthesis only, no audio device or network listener.
            english = old.load_tts()
            repeated_pcm = []
            for _ in range(2):
                chunks = english._synthesize("This is the same speech synthesis input.")
                pcm = np.concatenate([np.asarray(c.audio_int16_array).reshape(-1) for c in chunks])
                repeated_pcm.append(dict(samples=len(pcm), sha256=hashlib.sha256(pcm.tobytes()).hexdigest()))
            report["baseline_piper_repeat"] = repeated_pcm
            report["baseline_piper_is_stochastic"] = repeated_pcm[0] != repeated_pcm[1]
            utterances = ["Repeat the paragraph.", "Repeat paragraph one.", "Repeat paragraph two.",
                "Repeat paragraph three.", "What is the heading?", "Repeat the headings.",
                "Read from paragraph two.", "Skip paragraph one.", "Where am I?",
                "How many paragraphs are there?", "Continue reading.",
                "What voltage does the camera require?"]
            for text in utterances:
                chunks = english._synthesize(text)
                samples = np.concatenate([np.asarray(c.audio_int16_array).reshape(-1) for c in chunks])
                sr = chunks[0].sample_rate
                count = round(len(samples) * 16000 / sr)
                pcm = np.interp(np.arange(count) * sr / 16000, np.arange(len(samples)), samples).astype("<i2").tobytes()
                start = time.perf_counter()
                result = process.transcribe(pcm)
                expected, actual = parse_command(text), parse_command(result.get("text", ""))
                passed = (actual.action, actual.number) == (expected.action, expected.number)
                report["asr"].append(dict(input=text, transcript=result.get("text"),
                    passed=passed, wall_seconds=time.perf_counter()-start, inference_seconds=result.get("seconds")))
            silent = process.transcribe(bytes(32000))
            report["silence_rejected"] = not silent.get("text")
            # Warm voice process is left idle during enabled timing comparisons.
            phase = ["camera"]
            voice_service = VoiceService(ROOT, lambda: (phase[0], []), lambda *a, **kw: None,
                                         port=0, recognizer=process)
            voice_service.enabled = True  # No voice socket needed for this measurement.
            print("ASR finished", flush=True)
            ocr = old.load_ocr_engine("chinese")
            detector = CaptureDetector(ocr)
            old.run_ocr(ocr, str(images[0]), False, None)  # warm-up excluded
            saved_reference = json.loads((ROOT / "box6_validation/ocr_regression.json").read_text(encoding="utf-8"))["reference"]
            for image_path in images:
                outputs = []
                for label, module in (("box6", old), ("box7", new), ("box7", new), ("box6", old)):
                    text, regions, _, seconds = module.run_ocr(ocr, str(image_path), False, None)
                    outputs.append(dict(label=label, text=text, regions=regions, seconds=seconds,
                                        metrics=module.calculate_text_metrics(saved_reference, text)))
                signature = lambda row: [(r["source_text"], r["bbox"], r.get("region_type"), r.get("paragraph_number")) for r in row["regions"]]
                report["ocr"].append(dict(image=str(image_path), text_identical=all(x["text"] == outputs[0]["text"] for x in outputs),
                    regions_identical=all(signature(x) == signature(outputs[0]) for x in outputs),
                    timings=[dict(label=x["label"], seconds=x["seconds"]) for x in outputs], metrics=outputs[0]["metrics"]))
            # Replay actual full/partial/no-document frames from the retained session.
            raw = images[1].parent
            paths = sorted(raw.glob("*.jpg"))
            for path in [paths[0], paths[len(paths)//2], paths[-1]]:
                frame = cv2.imread(str(path))
                scale = min(1., old.CAPTURE_ANALYSIS_WIDTH / frame.shape[1])
                work = cv2.resize(frame, (round(frame.shape[1]*scale), round(frame.shape[0]*scale)), interpolation=cv2.INTER_AREA)
                prediction = detector.predict(work)
                engine = types.SimpleNamespace(predict=lambda image: prediction)
                a, b = old.analyze_capture_frame(frame, engine), new.analyze_capture_frame(frame, engine)
                fields = ["page_found", "page_complete", "focus_ok", "distance_ok", "lighting_ok", "row_count", "missing_sides", "text_readable"]
                report["capture"].append(dict(image=str(path), equal=all(a.get(k) == b.get(k) for k in fields),
                    assessment={k: a.get(k) for k in fields}))
            translator, tokenizer = old.load_nllb_translator_for_output("english", "urdu")
            urdu = old.load_urdu_tts(english)
            spell = old.load_spell_corrector()
            # Keep all audible effects disabled. Every text clip still goes
            # through real Piper synthesis and the actual RoutedTTS worker.
            for image_index, image_path in enumerate(images):
                target = "english" if image_index == 0 else "urdu"
                candidates = [("box6", old, False), ("box7_off", new, False), ("box7_idle_voice", new, True)]
                for name, module, enabled in candidates:
                    sink = SilentSink(); router = AudioRouter(local_player=sink)
                    english_tts = TracedTTS(english, router)
                    document_tts = english_tts if target == "english" else TracedTTS(urdu, router)
                    folder = destination / (name + "_" + target)
                    (folder / "captures").mkdir(parents=True, exist_ok=True)
                    events = []
                    phase[0] = "camera"
                    voice_service.enabled = enabled
                    def publish(kind, **data):
                        events.append((kind, data))
                        if kind == "stage": phase[0] = data["stage"]
                    app = types.SimpleNamespace(stop_requested=threading.Event(), publish_frame=lambda *a: None,
                        publish_event=publish, voice_service=voice_service if enabled else None)
                    PlaybackControls.voice = voice_service if enabled else None
                    with contextlib.ExitStack() as stack:
                        for key, value in {"_GUI_CONTROLLER": app, "AUDIO_ROUTER": router,
                            "PROJECT_DIR": str(folder), "CAPTURED_DIR": str(folder / "captures"),
                            "PARAGRAPH_TEST_DIR": str(folder), "SPELL_CORRECTOR": spell,
                            "_ACTIVE_DEBUG_RECORDER": None}.items():
                            stack.enter_context(patch.object(module, key, value))
                        for name_to_silence in ("beep_capture", "beep_failure", "tone_failure", "tone_success"):
                            stack.enter_context(patch.object(module, name_to_silence))
                        ok = module.process_and_speak(cv2.imread(str(image_path)), 1, "chinese", False, ocr, None,
                            translator, tokenizer, english_tts, ControlQueue(), ControlQueue(),
                            output_language=target, document_tts_module=document_tts)
                    completed = [data for kind, data in events if kind == "run_complete"]
                    assert ok and len(completed) == 1
                    report["pipeline"].append(dict(name=name, target=target, timings=completed[0]["timings"], spoken_requests=document_tts.spoken_requests,
                        regions=[{k: r.get(k) for k in ("region_id", "region_type", "paragraph_number", "source_text", "translated_text", "spoken_text", "bbox")}
                                 for r in completed[0]["paragraphs"]], pcm=sink.records))
                    english_tts.close()
                    if document_tts is not english_tts: document_tts.close()
                    router.close(); router = None
            for target in ("english", "urdu"):
                runs = [r for r in report["pipeline"] if r["target"] == target]
                report[target + "_regions_identical"] = all(r["regions"] == runs[0]["regions"] for r in runs)
                report[target + "_pcm_identical"] = all(r["pcm"] == runs[0]["pcm"] for r in runs)
                report[target + "_speech_requests_identical"] = all(r["spoken_requests"] == runs[0]["spoken_requests"] for r in runs)
                report[target + "_pcm_clip_counts_equal"] = all(len(r["pcm"]) == len(runs[0]["pcm"]) for r in runs)
            # Read the retained event log and replay its actual control payloads.
            from box6_runtime import control_action
            log_path = raw.parent / "events.jsonl"
            commands = [json.loads(line)["message"] for line in log_path.read_text(encoding="utf-8").splitlines()
                        if json.loads(line).get("event") == "pi_message"]
            report["recorded_controls"] = dict(source=str(log_path), count=len(commands), actions=[control_action(c) for c in commands])
    finally:
        if voice_service: voice_service.enabled = False
        process.close()
        if router: router.close()
        PlaybackControls.voice = None
    report["note"] = "Piper-to-Whisper uses synthetic test speech, not the user's accent or QCY microphone. PCM is generated and checked using a silent sink; physical playback latency is not measured. Timing samples are small and include ordinary run-to-run variability."
    (OUT / "model_checks.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    concise = {k: v for k,v in report.items() if k not in ("pipeline", "ocr", "asr")}
    concise["asr_passed"] = sum(r["passed"] for r in report["asr"])
    concise["asr_total"] = len(report["asr"])
    concise["pipeline_timings"] = [dict(name=r["name"], target=r["target"], **r["timings"]) for r in report["pipeline"]]
    print(json.dumps(concise, indent=2, default=str), flush=True)
    assert all(r["passed"] for r in report["asr"]), "Some ASR command fixtures failed; inspect model_checks.json"
    assert all(r["text_identical"] and r["regions_identical"] for r in report["ocr"])
    assert all(report[k] for k in ("silence_rejected", "english_regions_identical", "urdu_regions_identical",
        "english_speech_requests_identical", "urdu_speech_requests_identical",
        "english_pcm_clip_counts_equal", "urdu_pcm_clip_counts_equal"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--models", action="store_true")
    args = parser.parse_args()
    models() if args.models else units()
