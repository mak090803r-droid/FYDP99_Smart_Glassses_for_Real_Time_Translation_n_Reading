"""Bounded offline capture replay; never opens camera, audio, GUI or sockets.

Run with the project's full fydp Python path. The default benchmark uses six
saved frames (mostly 1080p), then optionally samples the recorded diagnostic
video. Video is useful for temporal checks, but is not equivalent to the
original 1080p incoming frames for focus scoring.
"""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import statistics
import sys
import time

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from box6_capture import CaptureDetector


def load_pipeline(path):
    spec = importlib.util.spec_from_file_location("capture_replay_pipeline", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def polygon_signature(results):
    signatures = []
    for result in results:
        for polygon in result.get("dt_polys", []):
            points = np.asarray(polygon, dtype=np.float32).reshape(-1, 2)
            # Ignore polygon order; detector geometry itself must be identical.
            signatures.append(tuple(sorted(tuple(map(float, p)) for p in points)))
    return sorted(signatures)


def session_paths():
    root = ROOT / "paragraph_test_outputs" / "live_debug_sessions"
    choices = [
        ("session_20260806_210058", 70),  # complete document
        ("session_20260806_210058", 99),  # genuinely clipped bottom lines
        ("session_20260806_210058", 8),   # sparse text
        ("session_20260806_205745", 1),   # no document
        ("session_20260901_121644", 3),   # no detected document
        ("session_20260903_134521", 10),  # recent complete document
    ]
    return [root / session / "raw_analysis_frames" / f"analysis_{index:05d}.jpg"
            for session, index in choices]


def benchmark(module, ocr, detector, paths, repeats):
    records = []
    for path in paths:
        frame = cv2.imread(str(path))
        if frame is None:
            raise RuntimeError("Missing replay input: " + str(path))
        scale = min(1.0, module.CAPTURE_ANALYSIS_WIDTH / frame.shape[1])
        work = cv2.resize(frame, (round(frame.shape[1] * scale),
                                  round(frame.shape[0] * scale)),
                          interpolation=cv2.INTER_AREA)
        full_seconds, detector_seconds = [], []
        equal = True
        for _ in range(repeats):
            started = time.perf_counter()
            full_result = list(ocr.predict(work))
            full_seconds.append(time.perf_counter() - started)
            started = time.perf_counter()
            det_result = detector.predict(work)
            detector_seconds.append(time.perf_counter() - started)
            equal = equal and polygon_signature(full_result) == polygon_signature(det_result)
        class SavedPrediction:
            def __init__(self, result):
                self.result = result

            def predict(self, image):
                return self.result

        full_assessment = module.analyze_capture_frame(frame, SavedPrediction(full_result))
        assessment = module.analyze_capture_frame(frame, SavedPrediction(det_result))
        compared_keys = ("page_found", "page_complete", "distance_ok", "focus_ok",
                         "lighting_ok", "text_readable", "row_count", "missing_sides",
                         "band_lap_min", "line_lap_p20", "light_median", "light_tile_std")
        assessment_equal = all(full_assessment.get(key) == assessment.get(key)
                               for key in compared_keys)
        _, quality = module.score_frame_quality(frame)
        record = {
            "image": str(path), "input_size": [frame.shape[1], frame.shape[0]],
            "polygons_equal": equal,
            "assessment_equal": assessment_equal,
            "polygon_count": len(polygon_signature(det_result)),
            "full_ocr_seconds_median": statistics.median(full_seconds),
            "detection_only_seconds_median": statistics.median(detector_seconds),
            "mode": detector.last_mode,
            "assessment": {key: assessment.get(key) for key in compared_keys},
            "gate_without_temporal_motion": module._capture_gate_passes(assessment),
            "guidance_without_temporal_motion": module._guidance_for_assessment(assessment),
            "postcapture_full_lap": quality["sharpness"],
        }
        records.append(record)
        print(json.dumps(record), flush=True)
    return records


def video_samples(module, detector, path, count):
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RuntimeError("Cannot open diagnostic video: " + str(path))
    total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    records = []
    try:
        for index in np.linspace(0, max(0, total - 1), count, dtype=int):
            capture.set(cv2.CAP_PROP_POS_FRAMES, int(index))
            ok, frame = capture.read()
            if not ok:
                continue
            assessment = module.analyze_capture_frame(frame, detector)
            records.append({
                "frame_index": int(index),
                "input_size": [frame.shape[1], frame.shape[0]],
                "state": module._guidance_for_assessment(assessment),
                "page_found": assessment["page_found"],
                "missing_sides": assessment["missing_sides"],
                "row_count": assessment["row_count"],
            })
    finally:
        capture.release()
    return records


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pipeline", type=Path, default=ROOT / "pipeline_cli_box5.py")
    parser.add_argument("--language", default="english")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--video", type=Path)
    parser.add_argument("--video-samples", type=int, default=6)
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("capture_replay.json"))
    args = parser.parse_args()
    # All weights already exist locally. Suppress remote model discovery.
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    module = load_pipeline(args.pipeline)
    ocr = module.load_ocr_engine(args.language)
    detector = CaptureDetector(ocr)
    warmup = cv2.imread(str(session_paths()[0]))
    warmup = cv2.resize(warmup, (960, 540), interpolation=cv2.INTER_AREA)
    list(ocr.predict(warmup))
    detector.predict(warmup)
    records = benchmark(module, ocr, detector, session_paths(), max(1, args.repeats))
    full = statistics.median([row["full_ocr_seconds_median"] for row in records])
    fast = statistics.median([row["detection_only_seconds_median"] for row in records])
    summary = {
        "pipeline": str(args.pipeline), "language": args.language,
        "all_polygon_sets_equal": all(row["polygons_equal"] for row in records),
        "all_assessments_equal": all(row["assessment_equal"] for row in records),
        "median_full_ocr_seconds": full, "median_detection_only_seconds": fast,
        "speedup": full / max(fast, 1e-9), "frames": records,
        "video_note": "Diagnostic video is downscaled; its focus metrics are not native capture evidence.",
        "video_samples": video_samples(module, detector, args.video, args.video_samples)
            if args.video else [],
    }
    args.output.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in summary.items()
                      if key not in ("frames", "video_samples")}), flush=True)


if __name__ == "__main__":
    main()
