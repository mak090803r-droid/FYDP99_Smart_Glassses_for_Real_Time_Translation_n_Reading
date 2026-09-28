"""
pipeline_cli_box7.py  (Box6 core plus optional push-to-talk voice input)
==================================================================================
Box7 adds a Voice Input checkbox, hold-G / long-hold Capture, numbered paragraph
and heading commands, and spoken-question transcription for Stage 2. See
BOX7_README.md. The Box6 core below retains detection-only capture analysis,
fresh-frame controls, reconnect/retry recovery, bounded local translation,
paragraph navigation and optional Pi audio. OCR preprocessing and paragraph
geometry remain baseline-equivalent. See BOX6_REPORT.md for measured evidence
and the physical hardware checks still required.

Historical evolution (not a list of new Box6 features):
Box5 added the Tk interface, English/Urdu output and evaluation metrics.
Box4/Box4b replace the pre-capture analysis/guidance subsystem:

  1. RELAXED EDGE MARGIN: Reduced from 3.5% to 1.5% of frame width so that
     larger text near page edges doesn't permanently trigger PAGE CUT OFF.
  2. PERCENTAGE-BASED EDGE CHECK: Page is only flagged as cut off when >15%
     of text boxes touch the edge (not just one stray box).
  3. ROTATION-SAFE PARAGRAPH SPACING: Uses line-centre pitch instead of the
     overlapping top/bottom edges of axis-aligned OCR boxes.
  4. REAL FIRST-LINE INDENT DETECTION: Confirms a paragraph boundary from the
     printed indent plus the extra paragraph spacing.
  5. HEADING SAFETY: Removes the height-only heading split that treated tilted
     body lines as headings.
  6. HEADING CLASSIFICATION: Uses geometry plus relative polygon font height;
     titles/headings remain translated and spoken but are not called paragraphs.
  7. TTS CONTROLS: A toggles pause/resume; S stops playback exactly as before.
  8. PHYSICAL PAGE GUARD: A strict paper mask plus the dominant document-text
     cluster rejects top/bottom/left/right crops instead of treating every
     scene OCR box as one page.
  9. SYNCHRONIZED STABILITY: Three distinct analyzed frames are required and
     the exact analyzed frame (not a newer unchecked frame) is captured.
 10. NON-BLOCKING GUIDANCE: Pre-rendered directional, lighting, motion, and
     focus prompts run outside the document TTS queue.
 11. PAGE-ROI QUALITY: Focus and illumination are measured on document text,
     including separate top/middle/bottom bands.
12. All other non-capture behaviour from box3d3 is preserved unchanged.

Box4b changes only capture/guidance behaviour: completeness is judged from
the detected text envelope, not the physical A4 boundary; sparse and mid-row
pages use the same rule as dense pages; and stability accepts three good
observations in the latest four so one detector flicker cannot reset progress.

Box4c keeps those gates and adds three recorded-session fixes: analysis runs
slightly more often, actionable guidance needs two matching observations, and
temporary low-coverage detections are not drawn as misleading boxes. Paragraph
grouping also accepts a short final line followed by a first-line indent, which
preserves printed paragraph breaks on curved or strongly tilted pages.

Usage:
    C:/Users/ali/Desktop/FYDP/fydp/Scripts/python.exe pipeline_cli_box6.py
    C:/Users/ali/Desktop/FYDP/fydp/Scripts/python.exe pipeline_cli_box6.py --cli
"""

import os
import sys

# PaddleX normally initializes every optional repository while it is imported.
# That pulls unrelated native modules (including pandas indexing) into the GUI
# startup path and leaves PaddleX poisoned if Windows Application Control blocks
# one DLL. OCR inference does not need that eager repository scan.
os.environ["PADDLE_PDX_EAGER_INIT"] = "False"
os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

# Redirected Windows consoles may default to cp1252. Chinese/Urdu text and
# capture status symbols must never abort an otherwise successful run.
for _console_stream in (sys.stdout, sys.stderr):
    if hasattr(_console_stream, "reconfigure"):
        try:
            _console_stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except (OSError, ValueError):
            pass

import re
import time
import json
import struct
import pickle
import queue
import threading
import socket
import unicodedata
import importlib
import importlib.util
import subprocess
import hashlib
import urllib.request
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText
import cv2
import numpy as np
import torch
import ctranslate2
import transformers
import symspellpy
from symspellpy import SymSpell, Verbosity
from PIL import Image, ImageTk
from box7_runtime import (ControlQueue, PlaybackControls, pending_actions,
                         terminal_action, recv_camera_message, capture_action)
from box6_audio import AudioRouter
from box6_translation import translate_regions
from box6_capture import CaptureDetector

# Cached local assets only; the environment flags above must be set before any
# third-party import that could transitively import PaddleX/Transformers.
AUDIO_ROUTER = None
_ACTIVE_DEBUG_RECORDER = None
STREAM_STALE_SECONDS = 3.0
STREAM_TIMEOUT_SECONDS = 8.0
_LAST_DOCUMENT = None
_PLAYBACK_ACTIVE = threading.Event()


# ══════════════════════════════════════════════════════════════════════════════
#  PATHS
# ══════════════════════════════════════════════════════════════════════════════
PIPELINE_DIR = os.path.dirname(os.path.abspath(__file__))


def _find_project_dir(start_dir):
    """Find the repository's Project directory from nested working copies."""
    current = os.path.abspath(start_dir)
    for _ in range(10):
        if (os.path.isdir(os.path.join(current, "1Pipeline")) and
                os.path.isdir(os.path.join(current, "pics"))):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    raise RuntimeError(f"Could not locate Project root above {start_dir}")


PROJECT_DIR  = _find_project_dir(PIPELINE_DIR)
PICS_DIR           = os.path.join(PROJECT_DIR, "pics")
CAPTURED_DIR       = os.path.join(PICS_DIR, "captured")
PARAGRAPH_TEST_DIR = os.path.join(PIPELINE_DIR, "paragraph_test_outputs")  # DEBUG — remove when done
os.makedirs(CAPTURED_DIR, exist_ok=True)
os.makedirs(PARAGRAPH_TEST_DIR, exist_ok=True)  # DEBUG — remove when done

# Persistent evidence for later capture/guidance tuning. Video encoding and
# analysis-image writes run on background threads so they do not hold up the
# receiver, OCR analysis, or the three-frame capture gate.
DEBUG_SESSION_RECORDING = True
DEBUG_VIDEO_FPS = 5.0
DEBUG_VIDEO_SIZE = (640, 360)
DEBUG_JPEG_QUALITY = 90
DEBUG_RAW_JPEG_QUALITY = 95
DEBUG_SESSION_ROOT = os.path.join(PARAGRAPH_TEST_DIR, "live_debug_sessions")

PORT = 9999

# Optional GUI bridge. It is deliberately presentation-only: every OCR,
# translation, capture-quality, and paragraph function below remains the Box4c
# implementation. In CLI mode this stays None and OpenCV windows behave exactly
# as before.
_GUI_CONTROLLER = None


def _gui_emit(event_type, **payload):
    controller = _GUI_CONTROLLER
    if controller is not None:
        controller.publish_event(event_type, **payload)


def _display_frame(window_name, frame, cli_size=None):
    """Route a frame to Tk in GUI mode or to the original OpenCV window."""
    if cli_size is not None:
        frame = cv2.resize(frame, cli_size)
    controller = _GUI_CONTROLLER
    if controller is not None:
        controller.publish_frame(window_name, frame)
        return
    cv2.imshow(window_name, frame)
    cv2.waitKey(1)


class _TeeStream:
    """Mirror stdout/stderr to the terminal and the session console log."""
    def __init__(self, original, log_file, lock):
        self._original = original
        self._log_file = log_file
        self._lock = lock

    def write(self, value):
        with self._lock:
            result = self._original.write(value)
            self._log_file.write(value)
        return result

    def flush(self):
        with self._lock:
            self._original.flush()
            self._log_file.flush()

    def __getattr__(self, name):
        return getattr(self._original, name)


class DebugSessionRecorder:
    """Record continuous video plus synchronized analysis and console evidence."""
    def __init__(self, output_root=DEBUG_SESSION_ROOT):
        os.makedirs(output_root, exist_ok=True)
        stamp = time.strftime("session_%Y%m%d_%H%M%S")
        session_dir = os.path.join(output_root, stamp)
        suffix = 1
        while os.path.exists(session_dir):
            session_dir = os.path.join(output_root, f"{stamp}_{suffix:02d}")
            suffix += 1
        os.makedirs(session_dir)
        self.session_dir = session_dir
        self.analysis_dir = os.path.join(session_dir, "analysis_frames")
        self.raw_analysis_dir = os.path.join(
            session_dir, "raw_analysis_frames")
        os.makedirs(self.analysis_dir)
        os.makedirs(self.raw_analysis_dir)

        self._started_wall = time.time()
        self._started_monotonic = time.monotonic()
        self._log_lock = threading.Lock()
        self._console_lock = threading.Lock()
        self._console_file = open(
            os.path.join(session_dir, "console.log"), "a",
            encoding="utf-8", buffering=1)
        self._metrics_file = open(
            os.path.join(session_dir, "analysis_metrics.jsonl"), "a",
            encoding="utf-8", buffering=1)
        self._events_file = open(
            os.path.join(session_dir, "events.jsonl"), "a",
            encoding="utf-8", buffering=1)
        self._video_times_file = open(
            os.path.join(session_dir, "video_timestamps.jsonl"), "a",
            encoding="utf-8", buffering=1)

        self._video_queue = queue.Queue(maxsize=8)
        self._artifact_queue = queue.Queue(maxsize=24)
        self._video_thread = threading.Thread(
            target=self._video_worker, name="debug-video-writer", daemon=True)
        self._artifact_thread = threading.Thread(
            target=self._artifact_worker, name="debug-artifact-writer", daemon=True)
        self._video_thread.start()
        self._artifact_thread.start()

        self._last_video_submit = 0.0
        self._analysis_index = 0
        self._video_written = 0
        self._video_dropped = 0
        self._analysis_dropped = 0
        self._video_path = None
        self._video_codec = None
        self._input_size = None
        self._old_stdout = None
        self._old_stderr = None
        self._closed = False
        self._write_manifest(active=True)
        self.record_event("session_started", session_dir=self.session_dir)

    @staticmethod
    def _json_safe(value):
        if isinstance(value, dict):
            return {str(k): DebugSessionRecorder._json_safe(v)
                    for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [DebugSessionRecorder._json_safe(v) for v in value]
        if isinstance(value, np.ndarray):
            return value.tolist()
        if isinstance(value, np.generic):
            return value.item()
        if isinstance(value, float) and not np.isfinite(value):
            return None
        return value

    def _base_record(self):
        return {
            "wall_time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "elapsed_seconds": round(
                time.monotonic() - self._started_monotonic, 4),
        }

    def _write_json_line(self, handle, payload):
        line = json.dumps(self._json_safe(payload), ensure_ascii=False)
        with self._log_lock:
            handle.write(line + "\n")
            handle.flush()

    def start_console_capture(self):
        if self._old_stdout is not None:
            return
        self._old_stdout, self._old_stderr = sys.stdout, sys.stderr
        sys.stdout = _TeeStream(
            self._old_stdout, self._console_file, self._console_lock)
        sys.stderr = _TeeStream(
            self._old_stderr, self._console_file, self._console_lock)

    def record_event(self, event_type, **details):
        payload = self._base_record()
        payload["event"] = event_type
        payload.update(details)
        self._write_json_line(self._events_file, payload)

    def submit_live_frame(self, frame, sequence, frame_timestamp):
        if self._closed or frame is None:
            return
        now = time.monotonic()
        if now - self._last_video_submit < 1.0 / DEBUG_VIDEO_FPS:
            return
        self._last_video_submit = now
        if self._input_size is None:
            self._input_size = (int(frame.shape[1]), int(frame.shape[0]))
        if self._video_queue.full():
            self._video_dropped += 1
            return
        # Receiver frames are immutable after decode; queue the stable reference
        # instead of copying six megabytes on every recorded frame.
        item = (frame, int(sequence), float(frame_timestamp), time.time())
        try:
            self._video_queue.put_nowait(item)
        except queue.Full:
            self._video_dropped += 1

    def record_analysis(self, payload, assessment, state, passed,
                        stability_count, motion=None):
        self._analysis_index += 1
        name = f"analysis_{self._analysis_index:05d}.jpg"
        relative_name = os.path.join("analysis_frames", name)
        raw_relative_name = os.path.join("raw_analysis_frames", name)
        overlay = draw_quality_overlay(
            payload["frame"], assessment, stability_count)
        try:
            self._artifact_queue.put_nowait((
                os.path.join(self.raw_analysis_dir, name),
                payload["frame"], DEBUG_RAW_JPEG_QUALITY))
        except queue.Full:
            self._analysis_dropped += 1
            raw_relative_name = None
        try:
            self._artifact_queue.put_nowait((
                os.path.join(self.analysis_dir, name), overlay,
                DEBUG_JPEG_QUALITY))
        except queue.Full:
            self._analysis_dropped += 1
            relative_name = None

        outer = assessment.get("outer_box") or {}
        record = self._base_record()
        record.update({
            "analysis_index": self._analysis_index,
            "raw_frame_file": raw_relative_name,
            "overlay_file": relative_name,
            "generation": payload.get("generation"),
            "frame_sequence": payload.get("sequence"),
            "frame_timestamp": payload.get("frame_timestamp"),
            "analysis_completed_at": payload.get("completed_at"),
            "analysis_error": payload.get("error"),
            "analysis_age": assessment.get("analysis_age"),
            "analysis_fresh": assessment.get("analysis_fresh"),
            "guidance_state": state,
            "guidance_text": GUIDANCE_PROMPTS.get(state, state),
            "gate_passed": bool(passed),
            "stability_count": int(stability_count),
            "page_found": assessment.get("page_found"),
            "page_complete": assessment.get("page_complete"),
            "content_complete": assessment.get("content_complete"),
            "text_envelope_complete": assessment.get(
                "text_envelope_complete"),
            "temporal_coverage_ok": assessment.get("temporal_coverage_ok"),
            "coverage_reference_rows": assessment.get(
                "coverage_reference_rows"),
            "distance_ok": assessment.get("distance_ok"),
            "text_readable": assessment.get("text_readable"),
            "focus_ok": assessment.get("focus_ok"),
            "lighting_ok": assessment.get("lighting_ok"),
            "motion_known": assessment.get("motion_known"),
            "motion_ok": assessment.get("motion_ok"),
            "motion_score": assessment.get("motion_score"),
            "motion_details": motion or {},
            "too_far": assessment.get("too_far"),
            "too_dark": assessment.get("too_dark"),
            "glare": assessment.get("glare"),
            "missing_sides": assessment.get("missing_sides", []),
            "physical_sides": assessment.get("physical_sides", []),
            "unreadable_sides": assessment.get("unreadable_sides", []),
            "row_count": assessment.get("row_count"),
            "all_box_count": assessment.get("all_box_count"),
            "cluster_box_count": len(assessment.get("boxes", [])),
            "outer_rect": outer.get("rect"),
            "text_margins": assessment.get("text_margins"),
            "text_clearance_lines": assessment.get("text_clearance_lines"),
            "median_line_ratio": assessment.get("median_line_ratio"),
            "page_long_ratio": assessment.get("page_long_ratio"),
            "band_laps": assessment.get("band_laps"),
            "band_lap_min": assessment.get("band_lap_min"),
            "line_lap_p20": assessment.get("line_lap_p20"),
            "light_median": assessment.get("light_median"),
            "light_tile_std": assessment.get("light_tile_std"),
            "outside_text_below": assessment.get("outside_text_below"),
        })
        self._write_json_line(self._metrics_file, record)

    def _open_video_writer(self):
        for filename, codec in (
                # MJPG/AVI is recoverable by most tools after an abrupt stop;
                # MP4 can lose its final moov atom and become wholly unreadable.
                ("live_feed.avi", "MJPG"),
                ("live_feed.mp4", "mp4v")):
            path = os.path.join(self.session_dir, filename)
            writer = cv2.VideoWriter(
                path, cv2.VideoWriter_fourcc(*codec),
                DEBUG_VIDEO_FPS, DEBUG_VIDEO_SIZE)
            if writer.isOpened():
                self._video_path = path
                self._video_codec = codec
                return writer
            writer.release()
        self.record_event("video_writer_failed")
        return None

    def _video_worker(self):
        writer = None
        try:
            while True:
                item = self._video_queue.get()
                if item is None:
                    break
                frame, sequence, frame_timestamp, received_wall = item
                if writer is None:
                    writer = self._open_video_writer()
                if writer is None:
                    self._video_dropped += 1
                    continue
                resized = cv2.resize(
                    frame, DEBUG_VIDEO_SIZE, interpolation=cv2.INTER_AREA)
                writer.write(resized)
                self._video_written += 1
                if self._video_written % 100 == 0:
                    self._write_manifest(active=True)
                timing = self._base_record()
                timing.update({
                    "video_frame_index": self._video_written,
                    "source_sequence": sequence,
                    "source_frame_timestamp": frame_timestamp,
                    "received_wall_time": received_wall,
                })
                self._write_json_line(self._video_times_file, timing)
        except Exception as exc:
            self.record_event("video_writer_exception", error=str(exc))
        finally:
            if writer is not None:
                writer.release()

    def _artifact_worker(self):
        try:
            while True:
                item = self._artifact_queue.get()
                if item is None:
                    break
                path, image, jpeg_quality = item
                ok = cv2.imwrite(
                    path, image,
                    [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
                if not ok:
                    self.record_event(
                        "analysis_image_write_failed", path=path)
        except Exception as exc:
            self.record_event("artifact_writer_exception", error=str(exc))

    def _write_manifest(self, active):
        manifest = {
            "script": os.path.basename(__file__),
            "session_dir": self.session_dir,
            "active": bool(active),
            "started_wall_time": self._started_wall,
            "ended_wall_time": None if active else time.time(),
            "video_target_fps": DEBUG_VIDEO_FPS,
            "video_size": list(DEBUG_VIDEO_SIZE),
            "input_size": list(self._input_size) if self._input_size else None,
            "video_file": (os.path.basename(self._video_path)
                           if self._video_path else None),
            "video_codec": self._video_codec,
            "video_frames_written": self._video_written,
            "video_frames_dropped": self._video_dropped,
            "analysis_records": self._analysis_index,
            "analysis_images_dropped": self._analysis_dropped,
            "capture_required_distinct_frames": globals().get(
                "CAPTURE_REQUIRED_DISTINCT"),
        }
        path = os.path.join(self.session_dir, "session_manifest.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2, ensure_ascii=False)

    def close(self):
        if self._closed:
            return
        self.record_event("session_ending")
        self._closed = True
        try:
            self._video_queue.put(None, timeout=2.0)
            self._artifact_queue.put(None, timeout=2.0)
            self._video_thread.join(timeout=10.0)
            self._artifact_thread.join(timeout=10.0)
            self._write_manifest(active=False)
        finally:
            if self._old_stdout is not None:
                sys.stdout, sys.stderr = self._old_stdout, self._old_stderr
                self._old_stdout = self._old_stderr = None
            for handle in (self._metrics_file, self._events_file,
                           self._video_times_file, self._console_file):
                try:
                    handle.flush()
                    handle.close()
                except Exception:
                    pass


_DEBUG_FAILURES_REPORTED = set()


def _safe_debug_call(recorder, method_name, *args, **kwargs):
    """Keep optional evidence collection from ever stopping the pipeline."""
    if recorder is None:
        return None
    try:
        return getattr(recorder, method_name)(*args, **kwargs)
    except Exception as exc:
        failure = (method_name, type(exc).__name__, str(exc))
        if failure not in _DEBUG_FAILURES_REPORTED:
            _DEBUG_FAILURES_REPORTED.add(failure)
            print(f"[DEBUG] {method_name} failed; pipeline continues: {exc}")
        return None

# ══════════════════════════════════════════════════════════════════════════════
#  WIRE PROTOCOL — must match piweb_cli.py
# ══════════════════════════════════════════════════════════════════════════════
MSG_JSON  = 0x01
MSG_FRAME = 0x02

_send_lock = threading.Lock()
OCR_LOCK = threading.Lock()
SPELL_CORRECTOR = None


def _recv_exact(sock, n):
    from box6_runtime import recv_exact
    return recv_exact(sock, n)


def send_json(sock, obj):
    data = json.dumps(obj).encode("utf-8")
    with _send_lock:
        sock.sendall(struct.pack("!BI", MSG_JSON, len(data)) + data)


def recv_msg(sock):
    """Bounded, validated decoder preserving the original camera wire format."""
    return recv_camera_message(sock)


# ══════════════════════════════════════════════════════════════════════════════
#  AUDIO TONES  (PC speakers)
# ══════════════════════════════════════════════════════════════════════════════
def _play_tone(freq_start, freq_end=None, duration=0.15, volume=0.3):
    sr = 22050
    t = np.arange(int(sr * duration), dtype=np.float32) / sr
    end = freq_start if freq_end is None else freq_end
    # Integrate the chirp frequency to obtain the phase.
    phase = 2 * np.pi * (freq_start * t + (end - freq_start) * t * t / (2 * duration))
    tone = (np.sin(phase) * volume).astype(np.float32)
    ramp = min(len(tone) // 2, int(0.01 * sr))
    if ramp:
        tone[:ramp] *= np.linspace(0, 1, ramp)
        tone[-ramp:] *= np.linspace(1, 0, ramp)
    try:
        if AUDIO_ROUTER is not None:
            AUDIO_ROUTER.play_samples(tone, sr)
        else:
            import sounddevice as sd
            sd.play(tone, samplerate=sr, blocking=True)
    except Exception as exc:
        print(f"[AUDIO] Tone unavailable: {exc}")

def tone_success():
    _play_tone(400, 800, 0.2)

def tone_failure():
    _play_tone(800, 400, 0.3)

def beep_capture():
    _play_tone(1000, 1000, 0.1)

def beep_ready():
    """Short high-pitched beep signaling frame quality is good enough."""
    _play_tone(1200, 1200, 0.12, volume=0.4)


def beep_failure():
    """Distinct retry signal used when a saved frame fails post-capture QA."""
    _play_tone(700, 280, 0.32, volume=0.4)


GUIDANCE_PROMPTS = {
    "checking": "Checking text.",
    "page_not_found": "Find the printed text.",
    "look_up": "Look up for the top text.",
    "look_down": "Look down for the bottom text.",
    "look_left": "Look left for the left text.",
    "look_right": "Look right for the right text.",
    "move_closer": "Move closer. Text is too small.",
    "move_back": "Move back so all text fits.",
    "too_dark": "Too dark. Improve the lighting.",
    "glare": "Glare detected. Change the angle.",
    "bad_lighting": "Lighting is uneven. Improve it.",
    "hold_still": "Hold still.",
    "blurry": "Text is blurry. Adjust distance.",
    "almost_ready": "Ready.",
}

_GUIDANCE_CLIPS = {}
_GUIDANCE_CLIPS_LOCK = threading.Lock()


def prepare_guidance_clips(tts_module):
    """Synthesize short prompts once; playback never touches the TTS queue."""
    if _GUIDANCE_CLIPS:
        return
    with _GUIDANCE_CLIPS_LOCK:
        if _GUIDANCE_CLIPS:
            return
        synthesize = getattr(tts_module, "_synthesize", None)
        if synthesize is None:
            raise RuntimeError("pipertts._synthesize is unavailable")
        print("[AUDIO] Pre-synthesizing real-time guidance prompts …")
        for state, prompt in GUIDANCE_PROMPTS.items():
            chunks = synthesize(prompt)
            if not chunks:
                raise RuntimeError(f"Piper produced no audio for {prompt!r}")
            sample_rate = chunks[0].sample_rate
            channels = chunks[0].sample_channels
            arrays = []
            for chunk in chunks:
                audio = np.asarray(chunk.audio_int16_array, dtype=np.int16)
                arrays.append(
                    audio.reshape(-1, channels).astype(np.float32) / 32768.0)
            # A short lead-in wakes the DAC without delaying the instruction.
            lead_in = np.zeros(
                (int(sample_rate * 0.06), channels), dtype=np.float32)
            _GUIDANCE_CLIPS[state] = (
                np.concatenate([lead_in] + arrays, axis=0), sample_rate)
        print(f"[AUDIO] {len(_GUIDANCE_CLIPS)} spoken guidance clips ready.")


def _play_guidance_clip(state):
    audio, sample_rate = _GUIDANCE_CLIPS[state]
    if AUDIO_ROUTER is not None:
        AUDIO_ROUTER.play_samples(audio, sample_rate)
    else:
        import sounddevice as sd
        sd.play(audio, samplerate=sample_rate, blocking=True)


class AudioGuidance:
    """Latest-state spoken feedback, independent of the blocking TTS queue."""
    def __init__(self, tts_module, state_cooldown=4.5, global_cooldown=0.80,
                 event_callback=None):
        prepare_guidance_clips(tts_module)
        self._queue = queue.Queue(maxsize=1)
        self._stop_event = threading.Event()
        self._last_by_state = {}
        self._last_any = 0.0
        self._last_state = None
        self._state_cooldown = state_cooldown
        self._global_cooldown = global_cooldown
        self._event_callback = event_callback
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def _record_event(self, event_type, **details):
        if self._event_callback is None:
            return
        try:
            self._event_callback(event_type, **details)
        except Exception:
            # Debug recording must never interfere with spoken guidance.
            pass

    def notify(self, state):
        if state not in GUIDANCE_PROMPTS or self._stop_event.is_set():
            return
        now = time.monotonic()
        if now - self._last_by_state.get(state, 0.0) < self._state_cooldown:
            return
        if now - self._last_any < self._global_cooldown:
            return
        opposites = {
            "look_up": "look_down", "look_down": "look_up",
            "look_left": "look_right", "look_right": "look_left",
        }
        if (opposites.get(self._last_state) == state
                and now - self._last_any < 4.0):
            return
        directional = set(opposites)
        if (self._last_state in directional
                and state != self._last_state
                and state != "almost_ready"
                and now - self._last_any < 2.6):
            # Give the wearer time to perform the last direction before a new
            # instruction can contradict it.
            return
        self._last_by_state[state] = now
        self._last_any = now
        self._last_state = state
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass
        try:
            self._queue.put_nowait(state)
            print(f"  [GUIDANCE] {GUIDANCE_PROMPTS[state]}")
            self._record_event(
                "guidance_queued", state=state,
                prompt=GUIDANCE_PROMPTS[state])
        except queue.Full:
            pass

    def _worker(self):
        while not self._stop_event.is_set():
            try:
                state = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if state is None:
                break
            try:
                self._record_event(
                    "guidance_started", state=state,
                    prompt=GUIDANCE_PROMPTS[state])
                _play_guidance_clip(state)
                self._record_event(
                    "guidance_finished", state=state,
                    prompt=GUIDANCE_PROMPTS[state])
            except Exception as exc:
                print(f"  [GUIDANCE] Playback failed: {exc}")
                self._record_event(
                    "guidance_failed", state=state, error=str(exc))

    def stop(self):
        self._stop_event.set()
        try:
            if AUDIO_ROUTER is not None:
                AUDIO_ROUTER.stop()
            else:
                import sounddevice as sd
                sd.stop()
        except Exception:
            pass
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        self._thread.join(timeout=0.5)


# ══════════════════════════════════════════════════════════════════════════════
#  IMAGE PREPROCESSING
# ══════════════════════════════════════════════════════════════════════════════
PREPROCESS_CONFIG = {
    # Winner of the 45-image / 225-prediction empirical sweep.
    "variant": "V_A_RAW_GRAYSCALE",
}


def preprocess_image(img_path: str):
    img = cv2.imread(img_path)
    if img is None:
        print(f"[ERROR] Could not read image: {img_path}")
        return None
    # V_A: deliberately no blur, sharpening, CLAHE, gamma, or resizing.
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # [V6 FIX] GaussianBlur removed — C525 plastic lens is already soft;
    # blurring on top of it merges thin character strokes and kills OCR on 11-12pt text.
    # gray     = cv2.GaussianBlur(gray, (3, 3), 0)


def unsharp_mask(image, sigma=1.0, strength=1.5):
    blurred = cv2.GaussianBlur(image, (0, 0), sigma)
    return cv2.addWeighted(image, 1.0 + strength, blurred, -strength, 0)


# ══════════════════════════════════════════════════════════════════════════════
#  NLLB 1.3B  CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════
if torch.cuda.is_available():
    NLLB_DEVICE       = "cuda"
    NLLB_COMPUTE_TYPE = "float16"
    os.environ["ARGOS_DEVICE_TYPE"] = "cuda"
    print("[INFO] Hardware Target: NVIDIA GPU (CUDA)")
else:
    NLLB_DEVICE       = "cpu"
    NLLB_COMPUTE_TYPE = "int8"
    print("[INFO] Hardware Target: CPU ONLY (Int8)")

NLLB_MODEL_DIR  = os.path.join(PROJECT_DIR, "nllb-200-1.3B-ct2")
NLLB_BASE_MODEL = "facebook/nllb-200-distilled-1.3B"

NLLB_LANG_MAP = {
    "zh": "zho_Hans",
    "fr": "fra_Latn",
    "es": "spa_Latn",
    "en": "eng_Latn",
}

PIPELINE_LANG_TO_NLLB = {
    "french":  "fr",
    "chinese": "zh",
    "spanish": "es",
}

# Box5-only mappings. The original Box4c dictionaries above are intentionally
# untouched so English-output regression behaviour remains byte-for-byte at
# the function level.
BOX5_NLLB_LANG_MAP = {
    **NLLB_LANG_MAP,
    "ur": "urd_Arab",
}
BOX5_SOURCE_LANG_TO_NLLB = {
    **PIPELINE_LANG_TO_NLLB,
    "english": "en",
}
BOX5_OUTPUT_LANG_TO_NLLB = {
    "english": "en",
    "urdu": "ur",
}

# PaddleOCR's language codes differ from NLLB's. PP-OCRv6 medium is a
# multilingual model. This mapping documents the source selection; the same
# explicitly cached multilingual weights are used for each supported script.
PADDLE_OCR_LANG_MAP = {
    "french": "en",
    "chinese": "ch",
    "spanish": "en",
    "english": "en",
}
_PADDLE_NATIVE_PREFLIGHT_DONE = False


# ══════════════════════════════════════════════════════════════════════════════
#  MODEL LOADERS
# ══════════════════════════════════════════════════════════════════════════════
def _preflight_paddle_native_dependencies():
    """Check the DLL seen in the real failure before PaddleX changes state."""
    global _PADDLE_NATIVE_PREFLIGHT_DONE
    if _PADDLE_NATIVE_PREFLIGHT_DONE:
        return
    try:
        importlib.import_module("pandas._libs.indexing")
    except (ImportError, OSError) as exc:
        detail = str(exc)
        if "Application Control policy" in detail or "DLL load failed" in detail:
            raise RuntimeError(
                "Windows blocked a required local OCR DLL (pandas indexing). "
                "Close this Box6 window and launch pipeline_cli_box6.py with the "
                "project fydp Python executable; the OCR environment itself must "
                f"be allowed by Windows Application Control. Original error: {detail}") from exc
        raise RuntimeError(f"Local OCR native dependency check failed: {detail}") from exc
    _PADDLE_NATIVE_PREFLIGHT_DONE = True


def _cached_paddle_model(name, required):
    """Fail locally if weights are missing; never invoke the model downloader."""
    root = os.environ.get("FYDP_PADDLE_MODEL_ROOT", os.path.expanduser("~/.paddlex/official_models"))
    path = os.path.join(root, name)
    missing = [filename for filename in required
               if not os.path.isfile(os.path.join(path, filename))]
    if missing:
        raise RuntimeError(f"Local model {name} is incomplete at {path}: {', '.join(missing)}")
    return path


def load_ocr_engine(language):
    _preflight_paddle_native_dependencies()
    try:
        from paddleocr import PaddleOCR
    except RuntimeError as exc:
        if "PDX has already been initialized" in str(exc):
            raise RuntimeError(
                "PaddleX was left partially initialized by an earlier failed OCR "
                "startup. Close this Box6 window once, relaunch Box6, and press "
                "Initialize only once; the corrected startup prevents recurrence.") from exc
        raise
    ocr_device = "gpu" if NLLB_DEVICE == "cuda" else "cpu"
    ocr_lang = PADDLE_OCR_LANG_MAP[language]
    print(f"[LOAD] OCR language audit: {language} -> PaddleOCR lang='{ocr_lang}'")
    print(f"[LOAD] Initializing PaddleOCR (onnxruntime, {ocr_device}) …")
    t = time.time()
    detector = _cached_paddle_model("PP-OCRv6_medium_det_onnx", ("inference.onnx", "inference.yml"))
    recognizer = _cached_paddle_model("PP-OCRv6_medium_rec_onnx", ("inference.onnx", "inference.yml"))
    ocr = PaddleOCR(
        text_detection_model_name="PP-OCRv6_medium_det",
        text_detection_model_dir=detector,
        text_recognition_model_name="PP-OCRv6_medium_rec",
        text_recognition_model_dir=recognizer,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        engine="onnxruntime",
        device=ocr_device,
    )
    print(f"[LOAD] PaddleOCR ready in {time.time()-t:.2f}s")
    return ocr


def load_unwarper():
    from paddleocr import TextImageUnwarping
    print("[LOAD] Initializing UVDoc unwarper …")
    t = time.time()
    model_dir = _cached_paddle_model("UVDoc", ("inference.pdiparams", "inference.json", "inference.yml"))
    unwarper = TextImageUnwarping(model_name="UVDoc", model_dir=model_dir, engine="paddle")
    print(f"[LOAD] UVDoc ready in {time.time()-t:.2f}s")
    return unwarper


def load_nllb_translator(language):
    return load_nllb_translator_for_output(language, "english")


def load_nllb_translator_for_output(language, output_language="english"):
    if language == "english" and output_language == "english":
        return None, None
    if output_language not in BOX5_OUTPUT_LANG_TO_NLLB:
        raise ValueError(f"Unsupported output language: {output_language}")
    required = ("model.bin", "config.json", "shared_vocabulary.json")
    missing = [name for name in required
               if not os.path.isfile(os.path.join(NLLB_MODEL_DIR, name))]
    if missing:
        raise RuntimeError(f"Local NLLB model is incomplete: {', '.join(missing)}")
    print(f"[LOAD] Loading local NLLB on {NLLB_DEVICE} ({NLLB_COMPUTE_TYPE}) ...")
    t0 = time.monotonic()
    tokenizer = transformers.AutoTokenizer.from_pretrained(
        NLLB_BASE_MODEL, local_files_only=True)
    translator = ctranslate2.Translator(
        NLLB_MODEL_DIR, device=NLLB_DEVICE, compute_type=NLLB_COMPUTE_TYPE,
        inter_threads=1, intra_threads=max(1, min(4, (os.cpu_count() or 4) // 2)))
    print(f"[LOAD] NLLB ready in {time.monotonic() - t0:.2f}s")
    return translator, tokenizer


def load_tts():
    if PIPELINE_DIR not in sys.path:
        sys.path.insert(0, PIPELINE_DIR)
    import pipertts
    print("[LOAD] Pre-loading Piper TTS voice …")
    t = time.time()
    for path in (pipertts._MODEL_PATH, pipertts._CONFIG_PATH):
        if not os.path.isfile(path) or os.path.getsize(path) == 0:
            raise RuntimeError(f"Local Piper voice asset missing: {path}")
    pipertts.preload()
    prepare_guidance_clips(pipertts)
    print(f"[LOAD] TTS ready in {time.time()-t:.2f}s")
    return AUDIO_ROUTER.wrap_tts(pipertts) if AUDIO_ROUTER is not None else pipertts


URDU_PIPER_VOICE = "ur_PK-fasih-medium"
URDU_PIPER_BASE_URL = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/main"
    "/ur/ur_PK/fasih/medium"
)
URDU_PIPER_FILE_INFO = {
    f"{URDU_PIPER_VOICE}.onnx": {
        "size": 63_532_015,
        "md5": "275113cbb8ffb29e3f8d51d53d266318",
    },
    f"{URDU_PIPER_VOICE}.onnx.json": {
        "size": 3_146,
        "md5": "e9d386b28cc8da844e6342325e04608b",
    },
}


def _file_md5(path):
    digest = hashlib.md5()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _urdu_voice_file_valid(path, expected):
    return (
        os.path.isfile(path) and
        os.path.getsize(path) == expected["size"] and
        _file_md5(path) == expected["md5"])


def _ensure_validated_urdu_voice_files(urdu_tts):
    """Validate existing voice assets without attempting a network download."""
    for filename, expected in URDU_PIPER_FILE_INFO.items():
        path = os.path.join(urdu_tts._MODELS_DIR, filename)
        if not _urdu_voice_file_valid(path, expected):
            raise RuntimeError(f"Missing or invalid local Urdu voice asset: {path}")


def load_urdu_tts(english_tts_module):
    """Load an independent Urdu Piper instance using the proven Box4c player."""
    module_path = getattr(english_tts_module, "__file__", None)
    if not module_path or not os.path.exists(module_path):
        raise RuntimeError("Could not locate the existing pipertts.py module")

    spec = importlib.util.spec_from_file_location(
        "pipertts_urdu_box5", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not create the Urdu Piper module loader")
    urdu_tts = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(urdu_tts)

    # The module is a separate instance, so its queue, pause state and loaded
    # voice cannot interfere with Box4c's English status/guidance voice.
    models_dir = getattr(english_tts_module, "_MODELS_DIR", None)
    if not models_dir:
        models_dir = os.path.join(
            PROJECT_DIR, "1Pipeline", "piper_models")
    urdu_tts._VOICE_NAME = URDU_PIPER_VOICE
    urdu_tts._MODELS_DIR = models_dir
    urdu_tts._MODEL_PATH = os.path.join(
        models_dir, f"{URDU_PIPER_VOICE}.onnx")
    urdu_tts._CONFIG_PATH = os.path.join(
        models_dir, f"{URDU_PIPER_VOICE}.onnx.json")
    urdu_tts._BASE_URL = URDU_PIPER_BASE_URL
    urdu_tts._voice = None

    print("[LOAD] Preparing local Urdu Piper voice ...")
    _ensure_validated_urdu_voice_files(urdu_tts)
    urdu_tts.preload()
    # Loading ONNX alone cannot reveal a missing phonemizer. Synthesize a short
    # phrase without playing it so an unusable Urdu voice falls back at startup.
    test_chunks = urdu_tts._synthesize(
        "\u06cc\u06c1 \u0627\u0631\u062f\u0648 \u0622\u0648\u0627\u0632 \u06a9\u06cc \u062c\u0627\u0646\u0686 \u06c1\u06d2\u06d4")
    if not test_chunks:
        raise RuntimeError("Urdu Piper produced no audio during startup test")
    print("[LOAD] Urdu Piper voice ready.")
    return AUDIO_ROUTER.wrap_tts(urdu_tts) if AUDIO_ROUTER is not None else urdu_tts


# ══════════════════════════════════════════════════════════════════════════════
#  PIPELINE STAGES
# ══════════════════════════════════════════════════════════════════════════════
def save_frame(frame, run_count):
    timestamp = time.strftime('%Y%m%d_%H%M%S')
    filename  = f"capture_cli_{run_count:03d}_{timestamp}.jpg"
    filepath  = os.path.join(CAPTURED_DIR, filename)
    # Save as high-quality JPEG (95%) instead of slow PNG
    if not cv2.imwrite(filepath, frame, [cv2.IMWRITE_JPEG_QUALITY, 95]):
        raise OSError(f"Could not save captured image: {filepath}")
    print(f"  💾 Saved → {filepath}")
    return filepath


def _extract_ocr_lines(result, image_shape):
    """Return PaddleOCR text, confidence, and matching boxes as line records."""
    image_h, image_w = image_shape[:2]
    records = []

    for res in result:
        texts = list(res.get("rec_texts", []))
        scores = list(res.get("rec_scores", []))
        boxes = res.get("rec_boxes")
        polygons = res.get("rec_polys", res.get("dt_polys", []))

        for index, raw_text in enumerate(texts):
            text = str(raw_text).strip()
            if not text:
                continue

            bbox = None
            polygon = None
            if polygons is not None and index < len(polygons):
                candidate_polygon = np.asarray(
                    polygons[index], dtype=np.float32).reshape(-1, 2)
                if candidate_polygon.size:
                    polygon = candidate_polygon
            if boxes is not None and index < len(boxes):
                box = np.asarray(boxes[index]).reshape(-1)
                if box.size >= 4:
                    bbox = tuple(int(round(value)) for value in box[:4])
            if bbox is None and polygon is not None:
                if polygon.size:
                    bbox = (
                        int(round(np.min(polygon[:, 0]))),
                        int(round(np.min(polygon[:, 1]))),
                        int(round(np.max(polygon[:, 0]))),
                        int(round(np.max(polygon[:, 1]))),
                    )
            if bbox is None:
                # Coordinate-free fallback keeps the original OCR flow usable.
                y1 = index * 24
                bbox = (0, y1, max(1, image_w), y1 + 20)

            x1, y1, x2, y2 = bbox
            x1 = max(0, min(image_w - 1, x1))
            y1 = max(0, min(image_h - 1, y1))
            x2 = max(x1 + 1, min(image_w, x2))
            y2 = max(y1 + 1, min(image_h, y2))
            font_height = float(y2 - y1)
            if polygon is not None and len(polygon) >= 3:
                _, rectangle_size, _ = cv2.minAreaRect(polygon)
                polygon_sides = [
                    float(side) for side in rectangle_size if side > 0]
                if polygon_sides:
                    font_height = min(polygon_sides)
            score = float(scores[index]) if index < len(scores) else 0.0
            records.append({
                "text": text,
                "score": score,
                "bbox": (x1, y1, x2, y2),
                "width": x2 - x1,
                "height": y2 - y1,
                "font_height": max(1.0, font_height),
                "center_x": (x1 + x2) / 2.0,
                "center_y": (y1 + y2) / 2.0,
            })

    return records


def merge_ocr_fragments_into_lines(records):
    """Merge PaddleOCR fragments that occupy the same printed text row."""
    if not records:
        return []

    median_height = max(
        8.0, float(np.median([record["height"] for record in records])))
    ordered = sorted(records, key=lambda record: (
        record["center_y"], record["bbox"][0]))
    rows = []

    for record in ordered:
        selected_row = None
        for row in reversed(rows[-4:]):
            center_difference = abs(record["center_y"] - row["center_y"])
            if center_difference > 0.30 * median_height:
                continue
            rx1, _, rx2, _ = record["bbox"]
            bx1, _, bx2, _ = row["bbox"]
            horizontal_gap = max(0, max(rx1, bx1) - min(rx2, bx2))
            if horizontal_gap <= 6.0 * median_height:
                selected_row = row
                break

        if selected_row is None:
            rows.append({
                "fragments": [record],
                "bbox": record["bbox"],
                "center_y": record["center_y"],
            })
            continue

        selected_row["fragments"].append(record)
        fragments = selected_row["fragments"]
        x1 = min(fragment["bbox"][0] for fragment in fragments)
        y1 = min(fragment["bbox"][1] for fragment in fragments)
        x2 = max(fragment["bbox"][2] for fragment in fragments)
        y2 = max(fragment["bbox"][3] for fragment in fragments)
        selected_row["bbox"] = (x1, y1, x2, y2)
        selected_row["center_y"] = float(np.mean([
            fragment["center_y"] for fragment in fragments]))

    merged = []
    for row in rows:
        fragments = sorted(
            row["fragments"], key=lambda fragment: fragment["bbox"][0])
        x1, y1, x2, y2 = row["bbox"]
        merged.append({
            "text": " ".join(fragment["text"] for fragment in fragments),
            "score": float(np.mean([
                fragment["score"] for fragment in fragments])),
            "bbox": (x1, y1, x2, y2),
            "width": x2 - x1,
            "height": y2 - y1,
            "font_height": float(np.median([
                fragment.get("font_height", fragment["height"])
                for fragment in fragments])),
            "center_x": (x1 + x2) / 2.0,
            "center_y": (y1 + y2) / 2.0,
        })
    return sorted(merged, key=lambda line: (
        line["center_y"], line["bbox"][0]))


def _horizontal_overlap_ratio(first, second):
    left = max(first[0], second[0])
    right = min(first[2], second[2])
    overlap = max(0, right - left)
    smaller_width = max(1, min(first[2] - first[0], second[2] - second[0]))
    return overlap / smaller_width


def group_ocr_lines_into_paragraphs(lines, image_shape):
    """
    Group OCR lines into printed paragraphs using rotation-safe geometry.

    PaddleOCR returns axis-aligned rectangles. On a tilted or unwarped page,
    neighbouring rectangles often overlap vertically even where the document
    contains a clear blank line. Paragraph spacing is therefore measured from
    line centre to line centre. A boundary is confirmed by the first-line
    indent deliberately present in the test documents; punctuation remains
    supporting evidence because OCR can drop full stops.
    """
    if not lines:
        return []

    ordered = sorted(lines, key=lambda line: (
        line["center_y"], line["bbox"][0]))
    heights = [line["height"] for line in ordered if line["height"] > 0]
    widths = [line["width"] for line in ordered if line["width"] > 0]
    median_height = max(
        8.0, float(np.median(heights)) if heights else 20.0)
    median_width = max(
        40.0, float(np.median(widths)) if widths else 200.0)

    # Estimate normal baseline-to-baseline spacing from the lower 70% of
    # plausible pitches. Paragraph gaps and missed OCR rows occupy the upper
    # tail, so neither can inflate ordinary line spacing.
    plausible_pitches = []
    pitch_floor = max(6.0, 0.22 * median_height)
    for previous, current in zip(ordered, ordered[1:]):
        pitch = current["center_y"] - previous["center_y"]
        overlap = _horizontal_overlap_ratio(
            previous["bbox"], current["bbox"])
        centre_delta = abs(previous["center_x"] - current["center_x"])
        if (pitch >= pitch_floor and
                (overlap >= 0.15 or
                 centre_delta <= 0.35 * median_width)):
            plausible_pitches.append(float(pitch))

    if plausible_pitches:
        plausible_pitches.sort()
        lower_count = max(1, int(np.ceil(0.70 * len(plausible_pitches))))
        typical_pitch = float(np.median(
            plausible_pitches[:lower_count]))
    else:
        typical_pitch = 0.70 * median_height
    typical_pitch = max(6.0, typical_pitch)

    body_like = [
        line for line in ordered if line["width"] >= 0.55 * median_width]
    if not body_like:
        body_like = ordered
    text_left = float(np.median([
        line["bbox"][0] for line in body_like]))
    text_right = float(np.median([
        line["bbox"][2] for line in body_like]))
    text_centre = (text_left + text_right) / 2.0

    # Keep layout decisions inside the dominant document column. OCR text from
    # a keyboard, monitor, or desk label can be valid recognition but must not
    # distort paragraph ordering/indentation or expand a paragraph rectangle.
    column_width = max(1.0, text_right - text_left)
    layout_inliers = []
    for line in ordered:
        overlap = max(
            0.0,
            min(line["bbox"][2], text_right) -
            max(line["bbox"][0], text_left))
        overlap_ratio = overlap / max(1.0, min(line["width"], column_width))
        far_from_column = (
            abs(line["center_x"] - text_centre) > 0.48 * median_width)
        weak_or_short = (
            line.get("score", 0.0) < 0.70
            or line["width"] < 0.30 * median_width)
        layout_outlier = (
            far_from_column and weak_or_short and overlap_ratio < 0.10)
        line["layout_outlier"] = bool(layout_outlier)
        if not layout_outlier:
            layout_inliers.append(line)
    if (len(layout_inliers) >= 3
            and len(layout_inliers) >= 0.70 * len(ordered)):
        ordered = layout_inliers
        body_like = [
            line for line in ordered if line["width"] >= 0.55 * median_width]
        if not body_like:
            body_like = ordered
        text_left = float(np.median([
            line["bbox"][0] for line in body_like]))
        text_right = float(np.median([
            line["bbox"][2] for line in body_like]))
        text_centre = (text_left + text_right) / 2.0

    centre_tolerance = max(24.0, 0.11 * median_width)
    indent_threshold = max(12.0, 0.60 * typical_pitch)
    strong_indent_threshold = max(16.0, 0.85 * typical_pitch)
    curved_indent_threshold = max(8.0, 0.40 * typical_pitch)
    body_font_candidates = [
        line.get("font_height", line["height"])
        for line in body_like
        if line.get("font_height", line["height"]) > 0]
    page_body_font_height = max(
        4.0,
        float(np.median(body_font_candidates))
        if body_font_candidates else median_height)

    def ends_sentence(text):
        return bool(re.search(
            r"[.!?。！？；;:][\"'”’\)\]]*$", text.strip()))

    def begins_lowercase(text):
        match = re.search(r"[A-Za-z]", text)
        return bool(match and match.group(0).islower())

    def title_case_ratio(text):
        words = re.findall(r"[A-Za-z][A-Za-z'-]*", text)
        if not words:
            return 0.0
        return sum(word[0].isupper() for word in words) / len(words)

    def local_body_font_height(index):
        nearby = []
        for other_index, other in enumerate(ordered):
            if other_index == index:
                continue
            if other["width"] < 0.48 * median_width:
                continue
            candidate_height = other.get("font_height", other["height"])
            if candidate_height <= 0:
                continue
            nearby.append((abs(other_index - index), candidate_height))
        nearby.sort(key=lambda item: item[0])
        local_values = [height for _, height in nearby[:6]]
        if local_values:
            return max(4.0, float(np.median(local_values)))
        return page_body_font_height

    def explicit_heading(line):
        text = line["text"].strip()
        return bool(
            re.match(
                r"(?i)^(?:[\[\(\|]?\s*[0-9oO]{1,3}\s*[\]\)\|]?\s*)?"
                r"chapter\s+[ivxlcdm0-9]+", text)
            or re.match(r"^\d+\.\d+\s+\S", text)
            or re.match(
                r"(?i)^(?:section|appendix)\s+[A-Z0-9IVXLC]+", text)
        )

    def starts_new_paragraph(line):
        text = line["text"].strip()
        return bool(
            re.match(
                r"^[\[\(\|]\s*[0-9oO]{1,3}\s*[\]\)\|]", text)
            or re.match(r"^[0-9oO]{1,3}\]", text)
            or re.match(
                r"^(?:[•●▪◦*-]|\d+[.)])\s+\S", text)
            or re.match(r"^\d+\.\d+\s+\S", text)
            or re.match(
                r"(?i)^chapter\s+[ivxlcdm0-9]+", text)
        )

    def pitch_before(index):
        if index <= 0:
            return 0.0
        return (
            ordered[index]["center_y"] -
            ordered[index - 1]["center_y"])

    def pitch_after(index):
        if index + 1 >= len(ordered):
            return 0.0
        return (
            ordered[index + 1]["center_y"] -
            ordered[index]["center_y"])

    def classify_visual_heading(index):
        line = ordered[index]
        text = line["text"].strip()
        local_font_height = local_body_font_height(index)
        font_scale = (
            line.get("font_height", line["height"]) /
            max(1.0, local_font_height))
        line["font_scale"] = float(font_scale)
        line["local_body_font_height"] = float(local_font_height)
        if explicit_heading(line):
            return True, "explicit_heading_pattern"
        if not text or len(text) > 120:
            return False, "ordinary_text"

        # A colon may legitimately finish a heading. Full sentence endings are
        # the stronger body-text cue that rejects a heading classification.
        full_sentence_ending = (
            ends_sentence(text) and
            not text.rstrip().endswith((":", ";", "：", "；")))
        if full_sentence_ending:
            return False, "sentence_ending"
        centred = (
            abs(line["center_x"] - text_centre) <= centre_tolerance)
        shortish = line["width"] <= 0.88 * median_width
        nearby_gap = max(pitch_before(index), pitch_after(index))
        title_like = title_case_ratio(text) >= 0.55
        title_words = re.findall(r"[A-Za-z][A-Za-z'-]*", text)
        block_overlap = max(
            0.0,
            min(line["bbox"][2], text_right) -
            max(line["bbox"][0], text_left))
        in_text_column = (
            block_overlap / max(1.0, min(line["width"], text_right - text_left))
            >= 0.45)

        # The first line is commonly a title. Title Case also handles the
        # left-aligned titles in multilingual sheets. Requiring the dominant
        # text column prevents a short background word becoming the title.
        if (index == 0 and in_text_column and
                pitch_after(index) >= 1.15 * typical_pitch and (
                    (centred and shortish) or
                    (title_like and len(title_words) >= 2))):
            return True, "top_title_geometry"

        if (in_text_column and centred and shortish and
                nearby_gap >= 1.25 * typical_pitch):
            return True, "centred_isolated_heading"

        # 20 pt over 16 pt is nominally 1.25x; 22 pt is 1.375x. Slightly
        # tolerant thresholds absorb OCR polygon measurement noise. Font size
        # is never sufficient by itself: layout evidence is still mandatory.
        if (in_text_column and font_scale >= 1.20 and shortish and (
                centred or title_like or
                nearby_gap >= 1.10 * typical_pitch)):
            return True, "large_font_plus_layout"
        if (in_text_column and font_scale >= 1.32 and
                (shortish or title_like) and
                nearby_gap >= 1.05 * typical_pitch):
            return True, "very_large_font_plus_spacing"
        return False, "ordinary_text"

    def continuation_left(index):
        # A first line is indented relative to following continuation lines.
        # Looking ahead avoids page-edge drift caused by perspective. Restrict
        # neighbours to the same text column so a desk/keyboard OCR outlier
        # cannot manufacture a several-hundred-pixel false indent.
        current = ordered[index]

        def same_column(candidates):
            matches = []
            for candidate in candidates:
                overlap = _horizontal_overlap_ratio(
                    current["bbox"], candidate["bbox"])
                centre_delta = abs(
                    current["center_x"] - candidate["center_x"])
                if (overlap >= 0.15
                        or centre_delta <= 0.35 * median_width):
                    matches.append(candidate)
            return matches

        following = same_column(
            ordered[index + 1:min(len(ordered), index + 7)])[:2]
        if following:
            return float(np.median([
                line["bbox"][0] for line in following]))
        preceding = same_column(
            ordered[max(0, index - 6):index])[-2:]
        if preceding:
            return float(np.median([
                line["bbox"][0] for line in preceding]))
        return float(ordered[index]["bbox"][0])

    heading_results = [
        classify_visual_heading(index) for index in range(len(ordered))]
    heading_flags = [result[0] for result in heading_results]
    for line, (is_heading, evidence) in zip(ordered, heading_results):
        line["is_heading"] = bool(is_heading)
        line["heading_evidence"] = evidence
    paragraph_lines = [[ordered[0]]]
    paragraph_reasons = ["start_of_page"]

    ordered[0]["paragraph_pitch"] = 0.0
    ordered[0]["paragraph_gap_ratio"] = 0.0
    ordered[0]["paragraph_indent_delta"] = 0.0
    ordered[0]["paragraph_break_reason"] = "start_of_page"

    for index in range(1, len(ordered)):
        previous = ordered[index - 1]
        current = ordered[index]
        previous_box = previous["bbox"]
        current_box = current["bbox"]
        pitch = current["center_y"] - previous["center_y"]
        gap_ratio = pitch / typical_pitch
        overlap = _horizontal_overlap_ratio(previous_box, current_box)
        centre_delta = abs(
            previous["center_x"] - current["center_x"])
        same_column = (
            overlap >= 0.15 or
            centre_delta <= 0.35 * median_width)

        indent_delta = current_box[0] - continuation_left(index)
        indented_start = indent_delta >= indent_threshold
        strong_indented_start = indent_delta >= strong_indent_threshold
        curved_indented_start = indent_delta >= curved_indent_threshold
        previous_terminal = ends_sentence(previous["text"])
        previous_short = previous["width"] <= 0.72 * median_width
        lowercase_continuation = begins_lowercase(current["text"])

        reason = None
        if not same_column:
            reason = "column_change"
        elif (heading_flags[index] and heading_flags[index - 1] and
                gap_ratio <= 1.35):
            reason = None
        elif heading_flags[index]:
            reason = "heading"
        elif heading_flags[index - 1]:
            reason = "after_heading"
        elif starts_new_paragraph(current):
            reason = "numbered_or_bulleted_start"
        elif (indented_start and gap_ratio >= 1.16) or (
                strong_indented_start and gap_ratio >= 1.04):
            reason = "first_line_indent_plus_spacing"
        elif (previous_short and curved_indented_start
              and gap_ratio >= 0.78
              and not lowercase_continuation):
            # On a curved/strongly tilted sheet, axis-aligned OCR rectangles
            # overlap vertically and can hide the printed blank row. A short
            # final line followed by a modest first-line indent remains stable
            # under that distortion and is present in the supplied test pages.
            reason = "short_final_line_plus_indent"
        elif (gap_ratio >= 1.70 and previous_short and
                not lowercase_continuation):
            reason = "blank_gap_after_short_line"
        elif (gap_ratio >= 2.20 and previous_terminal and
                not lowercase_continuation):
            reason = "large_gap_after_sentence"

        # A missed OCR row can also create a large centre pitch. Without an
        # indent, heading, short final row, or sentence boundary, keep the
        # lines together instead of inventing a paragraph.
        current["paragraph_pitch"] = float(pitch)
        current["paragraph_gap_ratio"] = float(gap_ratio)
        current["paragraph_indent_delta"] = float(indent_delta)
        current["paragraph_break_reason"] = reason or "continuation"

        if reason is None:
            paragraph_lines[-1].append(current)
        else:
            paragraph_lines.append([current])
            paragraph_reasons.append(reason)

    image_h, image_w = image_shape[:2]
    padding = max(4, int(round(0.18 * typical_pitch)))
    paragraphs = []
    for number, (grouped_lines, reason) in enumerate(
            zip(paragraph_lines, paragraph_reasons), start=1):
        x1 = max(
            0, min(line["bbox"][0] for line in grouped_lines) - padding)
        y1 = max(
            0, min(line["bbox"][1] for line in grouped_lines) - padding)
        x2 = min(
            image_w,
            max(line["bbox"][2] for line in grouped_lines) + padding)
        y2 = min(
            image_h,
            max(line["bbox"][3] for line in grouped_lines) + padding)
        confidence_values = [line["score"] for line in grouped_lines]
        paragraphs.append({
            "number": number,
            "region_id": number,
            "source_text": " ".join(
                line["text"] for line in grouped_lines),
            "bbox": (x1, y1, x2, y2),
            "lines": grouped_lines,
            "confidence": (
                float(np.mean(confidence_values))
                if confidence_values else 0.0),
            "break_reason": reason,
            "typical_line_pitch": typical_pitch,
        })

    body_number = 0
    heading_number = 0
    page_title_assigned = False
    for region in paragraphs:
        first_line = region["lines"][0]
        first_text = first_line["text"].strip()
        heading_region = bool(first_line.get("is_heading", False))
        centre_y = float(np.mean([
            line["center_y"] for line in region["lines"]]))
        explicit_section_marker = bool(re.match(
            r"(?i)^(?:chapter|section|appendix)\b|^\d+\.\d+\s+\S",
            first_text))

        if (heading_region and not page_title_assigned and
                centre_y <= 0.30 * image_h and
                not explicit_section_marker):
            region_type = "page_title"
            label = "TITLE"
            paragraph_number = None
            assigned_heading_number = None
            page_title_assigned = True
        elif heading_region:
            heading_number += 1
            region_type = "section_heading"
            label = f"H{heading_number}"
            paragraph_number = None
            assigned_heading_number = heading_number
        else:
            body_number += 1
            region_type = "paragraph"
            label = f"P{body_number}"
            paragraph_number = body_number
            assigned_heading_number = None

        region["region_type"] = region_type
        region["label"] = label
        region["paragraph_number"] = paragraph_number
        region["heading_number"] = assigned_heading_number
        region["font_scale"] = max(
            line.get("font_scale", 1.0) for line in region["lines"])
        region["heading_evidence"] = first_line.get(
            "heading_evidence", "ordinary_text")

    # Keep adjacent debug/preview rectangles separate. The boundary is the
    # midpoint between the final baseline of one paragraph and the first
    # baseline of the next, so padding cannot cover the printed blank row.
    for previous, current in zip(paragraphs, paragraphs[1:]):
        boundary = int(round((
            previous["lines"][-1]["center_y"] +
            current["lines"][0]["center_y"]) / 2.0))
        px1, py1, px2, py2 = previous["bbox"]
        cx1, cy1, cx2, cy2 = current["bbox"]
        previous["bbox"] = (
            px1, py1, px2, max(py1 + 1, min(py2, boundary)))
        current["bbox"] = (
            cx1, min(cy2 - 1, max(cy1, boundary)), cx2, cy2)

    return paragraphs

def _region_label(region):
    """Return the stable user-facing title/heading/paragraph label."""
    return region.get("label", f"P{region.get('number', '?')}")


def _region_counts(regions):
    return {
        "titles": sum(
            region.get("region_type") == "page_title" for region in regions),
        "headings": sum(
            region.get("region_type") == "section_heading" for region in regions),
        "paragraphs": sum(
            region.get("region_type", "paragraph") == "paragraph"
            for region in regions),
    }


def paragraph_page_location(paragraph, image_shape):
    """Describe a paragraph's vertical location for audio guidance."""
    image_h = max(1, image_shape[0])
    _, y1, _, y2 = paragraph["bbox"]
    relative_y = ((y1 + y2) / 2.0) / image_h
    if relative_y < 0.20:
        return "at the top of the page"
    if relative_y < 0.40:
        return "in the upper part of the page"
    if relative_y < 0.62:
        return "in the middle of the page"
    if relative_y < 0.82:
        return "in the lower part of the page"
    return "at the bottom of the page"


def draw_paragraph_overlay(image, paragraphs, active_index=None):
    """Draw numbered paragraph boxes, highlighting the paragraph being read."""
    canvas = image.copy()
    for index, paragraph in enumerate(paragraphs):
        x1, y1, x2, y2 = paragraph["bbox"]
        active = index == active_index
        color = (0, 220, 255) if active else (255, 120, 0)
        thickness = 4 if active else 2
        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, thickness)
        label = _region_label(paragraph)
        label_y = max(24, y1 - 8)
        cv2.putText(
            canvas, label, (x1, label_y), cv2.FONT_HERSHEY_SIMPLEX,
            0.75, color, 2, cv2.LINE_AA)

    counts = _region_counts(paragraphs)
    if active_index is not None and 0 <= active_index < len(paragraphs):
        active_region = paragraphs[active_index]
        active_type = active_region.get("region_type", "paragraph")
        if active_type == "page_title":
            status = "READING TITLE"
        elif active_type == "section_heading":
            status = f"READING HEADING {active_region.get('heading_number', '')}".strip()
        else:
            status = (
                f"READING PARAGRAPH {active_region.get('paragraph_number', '?')} "
                f"OF {counts['paragraphs']}")
    else:
        status_parts = [f"{counts['paragraphs']} PARAGRAPHS"]
        if counts["titles"]:
            status_parts.append("TITLE")
        if counts["headings"]:
            status_parts.append(f"{counts['headings']} HEADINGS")
        status = " + ".join(status_parts) + " DETECTED"
    cv2.rectangle(canvas, (0, 0), (min(canvas.shape[1], 620), 42), (20, 20, 20), -1)
    cv2.putText(
        canvas, status, (12, 29), cv2.FONT_HERSHEY_SIMPLEX,
        0.75, (255, 255, 255), 2, cv2.LINE_AA)
    return canvas


def show_paragraph_preview(image, paragraphs, active_index=None):
    """Display the OCR-coordinate image with paragraph mapping."""
    overlay = draw_paragraph_overlay(image, paragraphs, active_index)
    max_width, max_height = 1100, 800
    scale = min(
        1.0,
        max_width / max(1, overlay.shape[1]),
        max_height / max(1, overlay.shape[0]))
    if scale < 1.0:
        overlay = cv2.resize(
            overlay, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    _display_frame("Captured Image (OCR Target)", overlay)


# DEBUG — remove when done (saves overlay image + OCR text log per run for inspection)
def save_run_debug_outputs(
        run_count, img_path, ocr_image, paragraphs,
        extracted_text, language, spell_changes, output_language="english"):
    """
    Save a paragraph overlay image and a structured text log for this run.
    Files are written to PARAGRAPH_TEST_DIR so nothing pollutes the main log.
    """
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    base_name = f"run_{run_count:03d}_{timestamp}"

    # ── 1. Paragraph overlay image ──────────────────────────────────────────
    if ocr_image is not None:
        overlay = draw_paragraph_overlay(ocr_image, paragraphs, active_index=None)
        overlay_path = os.path.join(
            PARAGRAPH_TEST_DIR, f"{base_name}_overlay.jpg")
        cv2.imwrite(overlay_path, overlay, [cv2.IMWRITE_JPEG_QUALITY, 95])
        print(f"  [DEBUG] Overlay saved  → {overlay_path}")

    # ── 2. Structured text log ───────────────────────────────────────────────
    log_path = os.path.join(PARAGRAPH_TEST_DIR, f"{base_name}_ocr.txt")
    try:
        with open(log_path, "w", encoding="utf-8") as fout:
            fout.write("=" * 70 + "\n")
            fout.write(f"RUN          : {run_count}\n")
            fout.write(f"TIMESTAMP    : {timestamp}\n")
            fout.write(f"IMAGE FILE   : {os.path.basename(img_path)}\n")
            fout.write(f"LANGUAGE     : {language}\n")
            counts = _region_counts(paragraphs)
            fout.write(f"TEXT REGIONS : {len(paragraphs)}\n")
            fout.write(f"PAGE TITLES  : {counts['titles']}\n")
            fout.write(f"HEADINGS     : {counts['headings']}\n")
            fout.write(f"PARAGRAPHS   : {counts['paragraphs']}\n")
            fout.write("=" * 70 + "\n\n")

            fout.write("── RAW EXTRACTED TEXT ──\n")
            fout.write(extracted_text + "\n\n")

            fout.write("── CLASSIFIED REGION DETAIL ──\n")
            for paragraph in paragraphs:
                fout.write("-" * 60 + "\n")
                fout.write(
                    f"{_region_label(paragraph)} | "
                    f"region_id={paragraph.get('region_id', paragraph['number'])} | "
                    f"type={paragraph.get('region_type', 'paragraph')} | "
                    f"lines={len(paragraph['lines'])} | "
                    f"conf={paragraph['confidence']:.3f} | "
                    f"font_scale={paragraph.get('font_scale', 1.0):.2f} | "
                    f"heading_evidence={paragraph.get('heading_evidence', 'unknown')} | "
                    f"break={paragraph.get('break_reason', 'unknown')} | "
                    f"normal_pitch={paragraph.get('typical_line_pitch', 0.0):.2f} | "
                    f"bbox={paragraph['bbox']}\n")
                fout.write(f"  SOURCE    : {paragraph['source_text']}\n")
                fout.write("  LAYOUT LINES:\n")
                for line_number, line in enumerate(
                        paragraph["lines"], start=1):
                    fout.write(
                        f"    L{line_number:02d} | bbox={line['bbox']} | "
                        f"pitch={line.get('paragraph_pitch', 0.0):.2f} | "
                        f"gap_ratio={line.get('paragraph_gap_ratio', 0.0):.2f} | "
                        f"indent={line.get('paragraph_indent_delta', 0.0):.2f} | "
                        f"font_height={line.get('font_height', line['height']):.2f} | "
                        f"font_scale={line.get('font_scale', 1.0):.2f} | "
                        f"heading={line.get('heading_evidence', 'unknown')} | "
                        f"decision={line.get('paragraph_break_reason', 'unknown')} | "
                        f"text={line['text']}\n")
                if ((language != "english" or output_language != "english") and
                        "translated_text" in paragraph):
                    fout.write(
                        f"  TRANSLATED: {paragraph['translated_text']}\n")
                if "spoken_text" in paragraph:
                    fout.write(
                        f"  SPOKEN    : {paragraph['spoken_text']}\n")
                if paragraph.get("spell_changes"):
                    fout.write(
                        f"  SPELL FIXES: {paragraph['spell_changes']}\n")

            if spell_changes:
                fout.write("\n── SPELL CORRECTIONS SUMMARY ──\n")
                for region_label, original, replacement in spell_changes:
                    fout.write(
                        f"  {region_label}: {original!r} → {replacement!r}\n")

            fout.write("\n" + "=" * 70 + "\n")
        print(f"  [DEBUG] OCR log saved  → {log_path}")
    except Exception as save_err:
        print(f"  [DEBUG] Could not save OCR log: {save_err}")


def run_ocr(ocr_engine, img_path, book_mode, unwarper):
    t0 = time.monotonic()
    image = cv2.imread(img_path)
    if image is None:
        raise RuntimeError(f"Cannot read captured image: {img_path}")
    if book_mode:
        if unwarper is None:
            raise RuntimeError("Book mode requires the local UVDoc model")
        print("[STAGE 1] Unwarping curved page ...")
        with OCR_LOCK:
            results = list(unwarper.predict(image, batch_size=1))
        if not results or results[0].get("doctr_img") is None:
            raise RuntimeError("UVDoc returned no corrected image; please recapture")
        image = results[0]["doctr_img"]
        if not isinstance(image, np.ndarray) or image.size == 0:
            raise RuntimeError("UVDoc returned an invalid image")
    print("[STAGE 1] Raw grayscale preprocessing and PaddleOCR ...")
    processed = (cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
                 if image.ndim == 3 else image)
    ocr_input = cv2.cvtColor(processed, cv2.COLOR_GRAY2BGR)
    # Consume generators under the model lock as well as the predict call.
    with OCR_LOCK:
        result = list(ocr_engine.predict(ocr_input))
    raw_lines = _extract_ocr_lines(result, ocr_input.shape)
    lines = merge_ocr_fragments_into_lines(raw_lines)
    paragraphs = group_ocr_lines_into_paragraphs(lines, ocr_input.shape)
    extracted = " ".join(paragraph["source_text"] for paragraph in paragraphs)
    elapsed = time.monotonic() - t0
    print(f"[STAGE 1] OCR complete in {elapsed:.3f}s "
          f"({len(raw_lines)} detections, {len(lines)} lines, {len(paragraphs)} regions)")
    return extracted, paragraphs, ocr_input, elapsed


def run_translation_nllb(text, language, translator, tokenizer):
    result, elapsed = run_translation_nllb_paragraphs(
        [{"region_id": "text", "source_text": text}], language, translator, tokenizer)
    return result[0]["translated_text"] if result else "", elapsed


def _split_translation_units(text, src_lang_code):
    """Split a paragraph at sentence boundaries without losing punctuation."""
    if src_lang_code == "zh":
        units = re.split(r"(?<=[。？！!?])", text)
    else:
        units = re.split(r"(?<=[.!?])\s+", text)
    return [unit.strip() for unit in units if unit.strip()]


def _fit_translation_unit(unit, tokenizer, src_lang_code, max_tokens=400):
    """Keep every NLLB request safely below its context limit."""
    if len(tokenizer.encode(unit)) <= max_tokens:
        return [unit]

    is_chinese = src_lang_code == "zh"
    pieces = list(unit) if is_chinese else unit.split()
    joiner = "" if is_chinese else " "
    chunks = []
    current = []
    for piece in pieces:
        candidate = joiner.join(current + [piece])
        if current and len(tokenizer.encode(candidate)) > max_tokens:
            chunks.append(joiner.join(current).strip())
            current = [piece]
        else:
            current.append(piece)
    if current:
        chunks.append(joiner.join(current).strip())
    return [chunk for chunk in chunks if chunk]


def run_translation_nllb_paragraphs(paragraphs, language, translator, tokenizer):
    return run_translation_nllb_paragraphs_for_output(
        paragraphs, language, "english", translator, tokenizer)


def run_translation_nllb_paragraphs_for_output(
        paragraphs, language, output_language, translator, tokenizer):
    """Bounded paragraph-context translation, keeping the original region IDs."""
    return translate_regions(
        paragraphs, language, output_language, translator, tokenizer)


# ═══════════════════════════════════════════════════════════════════════════════
#  CONSERVATIVE OCR SPELL CORRECTION — SymSpell candidate generator
# ═══════════════════════════════════════════════════════════════════════════════

# These terms are always preserved. Generic rules below also preserve every
# acronym, mixed-case identifier, number, hyphenated term, and bracketed span.
_SPELL_PROTECTED_TERMS = {
    "airworthiness", "aerodynamic", "avionics", "barometer", "barometers",
    "bidirectional", "brushless", "chipset", "chipsets", "dynamometer",
    "electromagnetic", "fuselage", "maneuvering", "manoeuvring", "multirotor",
    "odometry", "powertrain", "telemetry", "topologically", "transmitter",
    "transmitters", "unwarping", "voxelization",
}

_BRACKETED_SPAN_RE = re.compile(
    r"(\[[^\]]*\]|\([^)]*\)|\{[^}]*\}|<[^>]*>)"
)
_PLAIN_TOKEN_RE = re.compile(r"^([^A-Za-z]*)([A-Za-z]+)([^A-Za-z]*)$")

# A correction must be a frequent dictionary word and a visually plausible
# OCR edit. These thresholds intentionally prefer a missed correction over a
# false replacement that changes the document's meaning.
_SPELL_MIN_WORD_LENGTH = 6
_SPELL_MIN_CANDIDATE_COUNT = 300_000
_SPELL_MIN_DOMINANCE_RATIO = 100


def load_spell_corrector():
    """Load SymSpell as a candidate finder; compound rewriting is disabled."""
    t0 = time.time()
    sym_spell = SymSpell(max_dictionary_edit_distance=2, prefix_length=7)
    dictionary_path = os.path.join(
        os.path.dirname(symspellpy.__file__),
        "frequency_dictionary_en_82_765.txt")
    if not sym_spell.load_dictionary(dictionary_path, term_index=0, count_index=1):
        raise RuntimeError(f"Could not load SymSpell dictionary: {dictionary_path}")
    print(f"[LOAD] Conservative SymSpell dictionary loaded in {time.time()-t0:.2f}s")
    return sym_spell


def _is_abbreviation_or_identifier(word):
    """Return True for acronyms, proper names, and mixed-case identifiers."""
    if len(word) <= 4:
        return True
    if word[0].isupper():
        return True
    if any(char.isupper() for char in word[1:]):
        return True
    if re.fullmatch(r"[A-Z]{2,}[a-z]?", word):
        return True
    return False


def _is_plausible_ocr_edit(source, candidate):
    """Allow only a small set of common visual OCR confusions."""
    source = source.lower()
    candidate = candidate.lower()
    if source == candidate:
        return False

    # Multi-character glyph confusions such as "intemal" -> "internal".
    def glyph_key(value):
        return value.replace("rn", "m").replace("cl", "d").replace("vv", "w")

    if glyph_key(source) == glyph_key(candidate):
        return True

    # One visually similar character substitution.
    if len(source) == len(candidate):
        differences = [
            (left, right)
            for left, right in zip(source, candidate)
            if left != right
        ]
        if len(differences) == 1:
            allowed_groups = (
                frozenset(("i", "l")),
                frozenset(("i", "j")),
                frozenset(("c", "e")),
                frozenset(("u", "v")),
                frozenset(("n", "r")),
                frozenset(("t", "f")),
            )
            return frozenset(differences[0]) in allowed_groups

    return False


def _correct_plain_segment(sym_spell, segment, changes):
    """Correct unprotected whitespace-delimited tokens without reformatting."""
    pieces = re.split(r"(\s+)", segment)
    for index, token in enumerate(pieces):
        if not token or token.isspace():
            continue

        # Never touch partial/malformed brackets, codes, measurements,
        # hyphenated technical terms (Li-Ion/Li-on), or slash-separated terms.
        if any(char in token for char in "[](){}<>0123456789-/\\+_=|@#%"):
            continue

        match = _PLAIN_TOKEN_RE.fullmatch(token)
        if match is None:
            continue
        prefix, word, suffix = match.groups()
        lower_word = word.lower()

        if len(word) < _SPELL_MIN_WORD_LENGTH:
            continue
        if _is_abbreviation_or_identifier(word):
            continue
        if lower_word in _SPELL_PROTECTED_TERMS:
            continue

        # A valid dictionary word is never rewritten. This prevents contextual
        # false positives such as "radio" -> "audio" or "array" -> "army".
        if lower_word in sym_spell.words:
            continue

        suggestions = sym_spell.lookup(
            lower_word,
            Verbosity.ALL,
            max_edit_distance=2,
            include_unknown=False)
        eligible = [
            suggestion for suggestion in suggestions
            if suggestion.distance in (1, 2)
            and suggestion.term.isalpha()
            and suggestion.count >= _SPELL_MIN_CANDIDATE_COUNT
            and _is_plausible_ocr_edit(lower_word, suggestion.term)
        ]
        if not eligible:
            continue

        eligible.sort(key=lambda suggestion: suggestion.count, reverse=True)
        best = eligible[0]
        if (len(eligible) > 1 and
                best.count < eligible[1].count * _SPELL_MIN_DOMINANCE_RATIO):
            continue

        replacement = best.term
        pieces[index] = f"{prefix}{replacement}{suffix}"
        changes.append((word, replacement))

    return "".join(pieces)


def run_spell_correction(sym_spell, text):
    """
    Apply only high-confidence OCR corrections.

    Text inside brackets is copied byte-for-byte. Abbreviations, identifiers,
    numbers, technical codes, and hyphenated words are never passed to
    SymSpell. Returns both the corrected text and an auditable change list.
    """
    if sym_spell is None or not text or not text.strip():
        return text, []

    changes = []
    chunks = _BRACKETED_SPAN_RE.split(text)
    corrected_chunks = []
    for chunk in chunks:
        if not chunk:
            continue
        if _BRACKETED_SPAN_RE.fullmatch(chunk):
            corrected_chunks.append(chunk)
        else:
            corrected_chunks.append(
                _correct_plain_segment(sym_spell, chunk, changes))
    return "".join(corrected_chunks), changes


def run_tts(tts_module, text, key_queue=None, event_queue=None):
    """Speak text. Press any key on PC or Pi to stop early."""
    print("[STAGE 3] Speaking … (press any key to stop)")
    t0 = time.time()

    # Clear out any old keystrokes/commands before starting playback
    if key_queue is not None:
        try:
            while not key_queue.empty():
                key_queue.get_nowait()
        except queue.Empty:
            pass

    if event_queue is not None:
        try:
            while not event_queue.empty():
                event_queue.get_nowait()
        except queue.Empty:
            pass

    tts_module.speak(text)

    stopped = False
    while tts_module.is_speaking():
        # Check PC keyboard queue
        if key_queue is not None and not key_queue.empty():
            try:
                while not key_queue.empty():
                    key_queue.get_nowait()
                tts_module.stop()
                stopped = True
                break
            except queue.Empty:
                pass

        # Check Pi command event queue
        if event_queue is not None and not event_queue.empty():
            try:
                stop_signal = False
                while not event_queue.empty():
                    evt = event_queue.get_nowait()
                    if evt.get("cmd") == "capture_from_pi" or evt.get("event") == "disconnect":
                        stop_signal = True
                if stop_signal:
                    tts_module.stop()
                    stopped = True
                    break
            except queue.Empty:
                pass

        time.sleep(0.05)

    tts_module.wait_until_done()

    # Clear any keystrokes/commands typed during playback to prevent queue build-up looping
    if key_queue is not None:
        try:
            while not key_queue.empty():
                key_queue.get_nowait()
        except queue.Empty:
            pass

    if event_queue is not None:
        try:
            while not event_queue.empty():
                event_queue.get_nowait()
        except queue.Empty:
            pass

    elapsed = time.time() - t0
    if stopped:
        print(f"[STAGE 3] TTS stopped after {elapsed:.3f}s")
    else:
        print(f"[STAGE 3] TTS finished in {elapsed:.3f}s")
    return elapsed


def _clear_queue_safely(target_queue):
    if target_queue is None:
        return
    try:
        while not target_queue.empty():
            target_queue.get_nowait()
    except queue.Empty:
        pass


def _speak_segment_with_stop(
        tts_module, text, key_queue=None, event_queue=None, controls=None):
    """Return finished/stop/next/previous/repeat/quit/disconnect."""
    controls = controls or PlaybackControls()
    action = controls.poll(tts_module, key_queue, event_queue)
    if action:
        return action
    # Do not start another segment while PTT is between paragraphs.
    voice = getattr(_GUI_CONTROLLER, "voice_service", None)
    while voice is not None and voice.enabled and voice.busy:
        action = controls.poll(tts_module, key_queue, event_queue)
        if action:
            return action
        time.sleep(0.04)
    while voice is not None and voice.enabled and tts_module.is_paused():
        action = controls.poll(tts_module, key_queue, event_queue)
        if action:
            return action
        time.sleep(0.04)
    _gui_emit("audio", active=True, paused=False)
    _PLAYBACK_ACTIVE.set()
    try:
        tts_module.speak(text)
        while tts_module.is_speaking():
            action = controls.poll(tts_module, key_queue, event_queue)
            _gui_emit("audio", active=True, paused=tts_module.is_paused())
            if action:
                return action
            if _GUI_CONTROLLER is not None and _GUI_CONTROLLER.stop_requested.is_set():
                tts_module.stop()
                return "quit"
            time.sleep(0.04)
        # Covers a stop pressed exactly as playback finishes.
        action = controls.poll(tts_module, key_queue, event_queue)
        if action:
            return action
        completed = tts_module.wait_until_done()
        if completed is False or getattr(tts_module, "last_error", None):
            raise RuntimeError(getattr(tts_module, "last_error", None) or "Audio playback failed")
        return "finished"
    finally:
        _PLAYBACK_ACTIVE.clear()
        _gui_emit("audio", active=False, paused=False)


def _run_tts_paragraphs_box6(
        tts_module, paragraphs, ocr_image,
        key_queue=None, event_queue=None, output_language="english", start_index=0):
    """Announce, highlight, and speak classified regions in reading order."""
    readable = [
        paragraph for paragraph in paragraphs
        if paragraph.get("spoken_text", "").strip()
    ]
    if not readable:
        return 0.0

    counts = _region_counts(readable)
    print(
        f"[STAGE 3] Speaking {len(readable)} classified region(s) in reading order "
        f"… (A pause/resume, S stop, R repeat, N next, P previous)")
    _gui_emit("stage", stage="audio", status="Speaking document")
    t0 = time.time()
    controls = PlaybackControls()
    stopped = False
    index = min(max(0, start_index), len(readable) - 1)
    while index < len(readable):
        paragraph = readable[index]
        if _LAST_DOCUMENT is not None:
            _LAST_DOCUMENT["index"] = index
        show_paragraph_preview(ocr_image, readable, active_index=index)
        location = paragraph_page_location(paragraph, ocr_image.shape)
        region_type = paragraph.get("region_type", "paragraph")
        label = _region_label(paragraph)
        _gui_emit(
            "active_region", index=index, label=label,
            region_id=paragraph.get("region_id"),
            source_text=paragraph.get("source_text", ""),
            spoken_text=paragraph.get("spoken_text", ""))
        if output_language == "urdu":
            urdu_locations = {
                "at the top of the page": "صفحے کے اوپری حصے میں",
                "in the upper part of the page": "صفحے کے بالائی حصے میں",
                "in the middle of the page": "صفحے کے درمیان میں",
                "in the lower part of the page": "صفحے کے نچلے حصے میں",
                "at the bottom of the page": "صفحے کے آخر میں",
            }
            urdu_location = urdu_locations.get(location, "صفحے پر")
            if region_type == "page_title":
                announcement = f"عنوان، {urdu_location}۔"
            elif region_type == "section_heading":
                announcement = (
                    f"سرخی {paragraph.get('heading_number', '')}، "
                    f"{urdu_location}۔")
            else:
                announcement = (
                    f"پیراگراف {paragraph.get('paragraph_number', '?')}، "
                    f"کل {counts['paragraphs']} میں سے، {urdu_location}۔")
        else:
            if region_type == "page_title":
                announcement = f"Title, {location}."
            elif region_type == "section_heading":
                announcement = (
                    f"Heading {paragraph.get('heading_number', '')}, "
                    f"{location}.")
            else:
                announcement = (
                    f"Paragraph {paragraph.get('paragraph_number', '?')} "
                    f"of {counts['paragraphs']}, {location}.")
        spoken_segment = f"{announcement} {paragraph['spoken_text'].strip()}"
        print(f"[STAGE 3] {label} — {location}")
        _safe_debug_call(_ACTIVE_DEBUG_RECORDER, "record_event",
                         "paragraph_started", region_id=paragraph.get("region_id"),
                         index=index, label=label,
                         audio_route="pi" if AUDIO_ROUTER and AUDIO_ROUTER.pi_enabled else "pc")
        action = "finished"
        # Bound each synthesis/playback request without changing paragraph IDs.
        segments = []
        remaining = spoken_segment
        while len(remaining) > 900:
            split = max(remaining.rfind(mark, 0, 901) for mark in (". ", "! ", "? ", "۔", "。"))
            if split < 300:
                split = remaining.rfind(" ", 0, 901)
            if split <= 0:
                split = 900
            else:
                split += 1
            segments.append(remaining[:split].strip())
            remaining = remaining[split:].strip()
        if remaining:
            segments.append(remaining)
        for segment in segments:
            action = _speak_segment_with_stop(
                tts_module, segment, key_queue, event_queue, controls)
            if action != "finished":
                break
        _safe_debug_call(_ACTIVE_DEBUG_RECORDER, "record_event",
                         "paragraph_playback_result", region_id=paragraph.get("region_id"),
                         index=index, action=action)
        from box7_runtime import VoiceAction
        if isinstance(action, VoiceAction):
            from box7_reading import handle, run
            choice = handle(sys.modules[__name__], action.command, key_queue, event_queue, readable, index)
            if choice is not None:
                indices, preview = choice
                return time.time() - t0 + run(sys.modules[__name__], tts_module, readable, ocr_image,
                    key_queue, event_queue, output_language, start_index=index,
                    selection=indices, preview=preview, resume_after_preview=True)
            stopped = True
            break
        elif action == "previous":
            index = max(0, index - 1)
        elif action == "repeat":
            continue
        elif action in ("stop", "quit", "disconnect"):
            stopped = True
            break
        else:
            index += 1

    show_paragraph_preview(ocr_image, readable, active_index=None)
    _gui_emit("active_region", index=None, label="", source_text="")
    elapsed = time.time() - t0
    if stopped:
        print(f"[STAGE 3] Region TTS stopped after {elapsed:.3f}s")
    else:
        print(f"[STAGE 3] Region TTS finished in {elapsed:.3f}s")
    return elapsed


# ══════════════════════════════════════════════════════════════════════════════
#  RECEIVER THREAD  — keeps the latest frame + capture signals in queues
# ══════════════════════════════════════════════════════════════════════════════
def run_tts_paragraphs(tts_module, paragraphs, ocr_image, key_queue=None,
                       event_queue=None, output_language="english", start_index=0):
    voice = getattr(_GUI_CONTROLLER, "voice_service", None)
    if voice is None or not voice.enabled:
        return _run_tts_paragraphs_box6(tts_module, paragraphs, ocr_image,
            key_queue, event_queue, output_language, start_index)
    from box7_reading import run
    return run(sys.modules[__name__], tts_module, paragraphs, ocr_image,
               key_queue, event_queue, output_language, start_index)


class FrameHolder:
    """Thread-safe holder for the most recent camera frame from the Pi."""
    def __init__(self):
        self._frame = None
        self._lock  = threading.Lock()
        self._sequence = 0
        self._timestamp = 0.0

    def update(self, frame):
        with self._lock:
            self._frame = frame
            self._sequence += 1
            self._timestamp = time.monotonic()
            return self._sequence, self._timestamp

    def clear(self):
        with self._lock:
            self._frame = None
            self._timestamp = 0.0

    def get(self):
        with self._lock:
            return self._frame.copy() if self._frame is not None else None

    def get_snapshot(self):
        """Return one frame together with the ID/time belonging to that frame."""
        with self._lock:
            if self._frame is None:
                return None, self._sequence, self._timestamp
            # receiver_loop replaces this array but never mutates it. Returning
            # the stable reference avoids copying a 1080p frame on every preview
            # iteration; the analysis worker also treats it as immutable.
            return self._frame, self._sequence, self._timestamp


def receiver_loop(sock, frame_holder, event_queue, debug_recorder=None):
    """Keep preview fresh and latch disconnects across capture/playback stages."""
    try:
        sock.settimeout(STREAM_TIMEOUT_SECONDS)
        while True:
            msg_type, data = recv_msg(sock)
            if msg_type == MSG_FRAME:
                sequence, frame_timestamp = frame_holder.update(data)
                _safe_debug_call(debug_recorder, "submit_live_frame",
                                 data, sequence, frame_timestamp)
            else:
                event_queue.put(data)
                _safe_debug_call(debug_recorder, "record_event",
                                 "pi_message", message=data)
    except Exception as exc:
        # A corrupt JPEG and a stalled camera must reach the same recovery path.
        frame_holder.clear()
        event_queue.put({"event": "disconnect", "error": str(exc)})
        _safe_debug_call(debug_recorder, "record_event",
                         "pi_disconnected", error=str(exc))


# ══════════════════════════════════════════════════════════════════════════════
#  KEYBOARD LISTENER  (PC side — A pauses TTS, S captures/stops, Q quits)
# ══════════════════════════════════════════════════════════════════════════════
def start_keyboard_listener(key_queue, stop_event=None):
    """CLI keyboard reader with a lifecycle; Tk handles its own GUI keys."""
    stop_event = stop_event or threading.Event()
    def listen():
        if sys.platform == "win32":
            import msvcrt
            while not stop_event.is_set():
                if msvcrt.kbhit():
                    key = msvcrt.getch().decode("utf-8", errors="ignore").lower()
                    if key in ("a", "s", "q", "n", "p", "r"):
                        key_queue.put(key)
                stop_event.wait(0.02)
        else:
            import termios, tty, select
            old_settings = termios.tcgetattr(sys.stdin)
            try:
                tty.setraw(sys.stdin.fileno())
                while not stop_event.is_set():
                    if select.select([sys.stdin], [], [], 0.05)[0]:
                        key = sys.stdin.read(1).lower()
                        if key in ("a", "s", "q", "n", "p", "r"):
                            key_queue.put(key)
            finally:
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
    thread = threading.Thread(target=listen, daemon=True, name="box6-keyboard")
    thread.start()
    return thread


# ══════════════════════════════════════════════════════════════════════════════
#  TCP SERVER
# ══════════════════════════════════════════════════════════════════════════════
def start_server(port):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('0.0.0.0', port))
    server.listen(1)
    print(f"\n[SERVER] Listening on port {port} …")
    print("  Waiting for Pi to connect …\n")
    client, addr = server.accept()
    print(f"[SERVER] ✅ Pi connected from {addr}")
    client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    client.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
    return server, client


# ══════════════════════════════════════════════════════════════════════════════
#  FRAME QUALITY SCORING
# ══════════════════════════════════════════════════════════════════════════════
QUALITY_THRESHOLD = 50  # retained for the legacy/post-save display only

# Box4c capture analysis is resolution-normalized and calibrated on the supplied
# 1920x1080 captures plus the recorded head-mounted sequence. These thresholds
# apply only to the 960-pixel analysis copy; the OCR target remains untouched.
CAPTURE_ANALYSIS_WIDTH = 960
CAPTURE_ANALYSIS_INTERVAL = 0.70
CAPTURE_REQUIRED_DISTINCT = 3
CAPTURE_STABILITY_WINDOW = 4
CAPTURE_MIN_STABLE_SECONDS = 1.25
CAPTURE_MAX_ANALYSIS_AGE = 1.35
CAPTURE_COVERAGE_HISTORY = 12
CAPTURE_COVERAGE_RATIO = 0.78
CAPTURE_FOCUS_BAND_MIN = 110.0
CAPTURE_LINE_FOCUS_MIN = 120.0
CAPTURE_LIGHT_MIN = 105.0
CAPTURE_LIGHT_MAX = 247.0
CAPTURE_LIGHT_TILE_STD_MAX = 24.0
CAPTURE_PAPER_BORDER_FRACTION = 0.20
CAPTURE_PAPER_BORDER_RUN = 0.20
# A text box needs only a small fraction of one detected line-height between it
# and the image edge. This scales correctly for 16/18 pt text, camera distance,
# and perspective. It also rejects genuinely clipped text while ignoring blank
# A4 paper margins and the physical paper contour.
CAPTURE_TEXT_CLEARANCE_LINES = 0.50
CAPTURE_MIN_LINE_HEIGHT_RATIO = 0.013
CAPTURE_EXTRA_TEXT_FOCUS_MIN = 80.0
CAPTURE_OUTSIDE_TEXT_MEAN_MIN = 3.60
CAPTURE_OUTSIDE_TEXT_FRACTION_MIN = 0.080
POST_CAPTURE_LAP_MIN = 100.0
# Inter-sample motion is measured about 0.90 seconds apart. Normal head-mounted
# parallax can move a still-sharp page several percent between those samples;
# actual blur remains guarded independently by the page-band and line focus
# gates below.
CAPTURE_MOTION_SHIFT_MAX = 0.065
CAPTURE_MOTION_GEOMETRY_MAX = 0.075
CAPTURE_MOTION_AREA_CHANGE_MAX = 0.35
CAPTURE_MOTION_ANGLE_MAX = 8.0


def score_frame_quality(frame):
    """Compatibility score used after save; capture decisions use hard gates."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    laplacian_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    sharpness_pts = int(np.clip((laplacian_var - 50.0) / 250.0 * 40.0, 0, 40))
    mean_brightness = float(np.mean(gray))
    if 80 <= mean_brightness <= 180:
        brightness_pts = 30
    elif mean_brightness < 80:
        brightness_pts = max(0, int(mean_brightness / 80 * 30))
    else:
        brightness_pts = max(0, int((255 - mean_brightness) / 75 * 30))
    mid_h, mid_w = h // 2, w // 2
    quadrants = [gray[:mid_h, :mid_w], gray[:mid_h, mid_w:],
                 gray[mid_h:, :mid_w], gray[mid_h:, mid_w:]]
    quad_std = float(np.std([float(np.mean(q)) for q in quadrants]))
    evenness_pts = int(np.clip((40.0 - quad_std) / 35.0 * 30.0, 0, 30))
    score = min(100, sharpness_pts + brightness_pts + evenness_pts)
    details = {
        "sharpness": round(laplacian_var, 1),
        "sharpness_pts": sharpness_pts,
        "brightness": round(mean_brightness, 1),
        "brightness_pts": brightness_pts,
        "evenness_std": round(quad_std, 1),
        "evenness_pts": evenness_pts,
    }
    return score, details


# ══════════════════════════════════════════════════════════════════════════════
#  TEXT REGION DETECTION & OUTER PAGE BOX GENERATOR
# ══════════════════════════════════════════════════════════════════════════════
def _longest_true_run(values):
    longest = current = 0
    for value in values:
        if bool(value):
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _extract_detection_boxes(ocr_result, work):
    """Convert Paddle polygons into geometry/local-background records."""
    gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    boxes = []
    for result_item in ocr_result:
        try:
            polygons = result_item.get("dt_polys", [])
        except AttributeError:
            polygons = result_item["dt_polys"] if "dt_polys" in result_item else []
        for polygon in polygons:
            poly = np.asarray(polygon, dtype=np.float32).reshape(-1, 2)
            if len(poly) < 4:
                continue
            (cx, cy), (rect_w, rect_h), angle = cv2.minAreaRect(poly)
            if rect_w < rect_h:
                rect_w, rect_h = rect_h, rect_w
                angle += 90.0
            angle = ((angle + 90.0) % 180.0) - 90.0
            x1, y1 = poly.min(axis=0)
            x2, y2 = poly.max(axis=0)
            xa, xb = max(0, int(x1) - 4), min(w, int(x2) + 5)
            ya, yb = max(0, int(y1) - 4), min(h, int(y2) + 5)
            local = gray[ya:yb, xa:xb]
            background = float(np.percentile(local, 80)) if local.size else 0.0
            boxes.append({
                "poly": poly,
                "cx": float(cx), "cy": float(cy),
                "w": float(rect_w), "h": float(rect_h),
                "angle": float(angle),
                "bbox": (float(x1), float(y1), float(x2), float(y2)),
                "bg80": background,
            })
    return boxes


def _group_document_rows(boxes):
    if not boxes:
        return [], 0.0, 0.0
    median_height = float(np.median([box["h"] for box in boxes]))
    rows = []
    for box in sorted(boxes, key=lambda item: item["cy"]):
        if not rows or box["cy"] - rows[-1][-1]["cy"] > 0.65 * median_height:
            rows.append([box])
        else:
            rows[-1].append(box)
    centres = np.asarray([np.mean([item["cy"] for item in row]) for row in rows])
    gaps = np.diff(centres)
    median_gap = float(np.median(gaps)) if len(gaps) else 0.0
    max_gap_ratio = float(np.max(gaps) / max(median_gap, 1e-6)) if len(gaps) else 0.0
    return rows, median_gap, max_gap_ratio


def _dominant_document_cluster(boxes, width, height):
    """Keep the dense paper text and discard monitor/keyboard OCR outliers."""
    candidates = [
        box for box in boxes
        if (box["bg80"] >= 95.0 and 4.0 <= box["h"] <= 0.09 * height
            and box["w"] >= 1.15 * box["h"])
    ]
    if not candidates:
        return None

    angle_histogram = np.zeros(36, dtype=np.float64)
    for box in candidates:
        angle = max(-44.9, min(44.9, box["angle"]))
        angle_histogram[int((angle + 45.0) // 2.5)] += min(box["w"], 0.35 * width)
    dominant_angle = -45.0 + (int(np.argmax(angle_histogram)) + 0.5) * 2.5
    angle_filtered = [
        box for box in candidates
        if abs(((box["angle"] - dominant_angle + 90.0) % 180.0) - 90.0) <= 13.0
    ]
    if len(angle_filtered) >= 3:
        candidates = angle_filtered

    median_height = float(np.median([box["h"] for box in candidates]))
    parent = list(range(len(candidates)))

    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(first, second):
        first, second = find(first), find(second)
        if first != second:
            parent[second] = first

    for first_index, first in enumerate(candidates):
        ax1, _, ax2, _ = first["bbox"]
        for second_index in range(first_index + 1, len(candidates)):
            second = candidates[second_index]
            bx1, _, bx2, _ = second["bbox"]
            delta_y = abs(first["cy"] - second["cy"])
            delta_x = abs(first["cx"] - second["cx"])
            overlap = max(0.0, min(ax2, bx2) - max(ax1, bx1))
            minimum_width = max(1.0, min(first["w"], second["w"]))
            horizontally_close = delta_x <= max(
                0.19 * width, 0.65 * (first["w"] + second["w"]))
            if (delta_y <= max(7.0 * median_height, 0.09 * height)
                    and (overlap / minimum_width >= 0.08 or horizontally_close)):
                union(first_index, second_index)

    groups = {}
    for index, box in enumerate(candidates):
        groups.setdefault(find(index), []).append(box)

    def cluster_score(group):
        x_values = [value for box in group for value in (box["bbox"][0], box["bbox"][2])]
        y_values = [value for box in group for value in (box["bbox"][1], box["bbox"][3])]
        span = ((max(x_values) - min(x_values)) * (max(y_values) - min(y_values))
                / float(width * height))
        return (2.0 * len(group)
                + sum(min(box["w"] / width, 0.30) for box in group)
                + 10.0 * min(span, 0.50)
                + float(np.median([box["bg80"] for box in group])) / 80.0)

    group = max(groups.values(), key=cluster_score)
    x1 = min(box["bbox"][0] for box in group)
    y1 = min(box["bbox"][1] for box in group)
    x2 = max(box["bbox"][2] for box in group)
    y2 = max(box["bbox"][3] for box in group)
    rows, median_gap, max_gap_ratio = _group_document_rows(group)
    return {
        "boxes": group,
        "rect": (float(x1), float(y1), float(x2), float(y2)),
        "rows": rows,
        "row_count": len(rows),
        "median_height": float(np.median([box["h"] for box in group])),
        "median_gap": median_gap,
        "max_gap_ratio": max_gap_ratio,
        "angle": float(np.median([box["angle"] for box in group])),
        "background": float(np.median([box["bg80"] for box in group])),
    }


def _strict_paper_component(work, text_rect):
    """Return strict low-chroma paper evidence seeded by the document text."""
    h, w = work.shape[:2]
    x1, y1, x2, y2 = [int(round(value)) for value in text_rect]
    x1, x2 = max(0, x1), min(w, x2)
    y1, y2 = max(0, y1), min(h, y2)
    if x2 - x1 < 20 or y2 - y1 < 20:
        return None

    hsv = cv2.cvtColor(work, cv2.COLOR_BGR2HSV)
    lab = cv2.cvtColor(work, cv2.COLOR_BGR2LAB)
    inner_hsv, inner_lab = hsv[y1:y2, x1:x2], lab[y1:y2, x1:x2]
    saturation, value = inner_hsv[:, :, 1], inner_hsv[:, :, 2]
    sample_mask = ((saturation <= np.percentile(saturation, 55))
                   & (value >= np.percentile(value, 62)))
    samples, sample_hsv = inner_lab[sample_mask], inner_hsv[sample_mask]
    if len(samples) < 200:
        samples = inner_lab.reshape(-1, 3)
        sample_hsv = inner_hsv.reshape(-1, 3)
    median_lab = np.median(samples, axis=0).astype(np.float32)
    median_s = float(np.median(sample_hsv[:, 1]))
    median_v = float(np.median(sample_hsv[:, 2]))

    lightness = lab[:, :, 0].astype(np.float32)
    channel_a = lab[:, :, 1].astype(np.float32)
    channel_b = lab[:, :, 2].astype(np.float32)
    saturation_all = hsv[:, :, 1].astype(np.float32)
    value_all = hsv[:, :, 2].astype(np.float32)
    chroma_distance = np.hypot(
        channel_a - median_lab[1], channel_b - median_lab[2])
    saturation_limit = min(35.0, max(20.0, median_s + 15.0))
    mask = (
        (saturation_all <= saturation_limit)
        & (chroma_distance <= 13.0)
        & (value_all >= max(70.0, median_v - 100.0))
        & (lightness >= max(65.0, median_lab[0] - 105.0))
    ).astype(np.uint8) * 255

    kernel_size = max(7, int(round(w / 105.0)) | 1)
    closed = cv2.morphologyEx(
        mask, cv2.MORPH_CLOSE,
        np.ones((kernel_size, kernel_size), np.uint8), iterations=2)
    closed = cv2.morphologyEx(
        closed, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8), iterations=1)

    count, labels, stats, _ = cv2.connectedComponentsWithStats(closed, 8)
    seed_x1 = x1 + int(0.08 * (x2 - x1))
    seed_x2 = x2 - int(0.08 * (x2 - x1))
    seed_y1 = y1 + int(0.03 * (y2 - y1))
    seed_y2 = y2 - int(0.03 * (y2 - y1))
    best_label, best_score = None, -1e30
    seed_area = max(1, (seed_x2 - seed_x1) * (seed_y2 - seed_y1))
    for label in range(1, count):
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area < 0.018 * w * h:
            continue
        overlap = int(np.count_nonzero(
            labels[seed_y1:seed_y2, seed_x1:seed_x2] == label))
        if overlap < 100:
            continue
        score = 7.0 * overlap / seed_area + 0.4 * area / float(w * h)
        if score > best_score:
            best_label, best_score = label, score
    if best_label is None:
        return None

    selected = (labels == best_label).astype(np.uint8) * 255
    contours, _ = cv2.findContours(
        selected, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    contour = max(contours, key=cv2.contourArea)
    hull = cv2.convexHull(contour)
    bbox = cv2.boundingRect(hull)
    strip = max(4, int(round(0.012 * min(w, h))))
    fractions = {
        "L": float(np.count_nonzero(selected[:, :strip])) / float(h * strip),
        "T": float(np.count_nonzero(selected[:strip, :])) / float(w * strip),
        "R": float(np.count_nonzero(selected[:, w - strip:])) / float(h * strip),
        "B": float(np.count_nonzero(selected[h - strip:, :])) / float(w * strip),
    }
    active_profiles = {
        "L": np.mean(selected[:, :strip] > 0, axis=1) > 0.40,
        "T": np.mean(selected[:strip, :] > 0, axis=0) > 0.40,
        "R": np.mean(selected[:, w - strip:] > 0, axis=1) > 0.40,
        "B": np.mean(selected[h - strip:, :] > 0, axis=0) > 0.40,
    }
    runs = {
        "L": _longest_true_run(active_profiles["L"]) / float(h),
        "T": _longest_true_run(active_profiles["T"]) / float(w),
        "R": _longest_true_run(active_profiles["R"]) / float(h),
        "B": _longest_true_run(active_profiles["B"]) / float(w),
    }
    return {
        "contour": contour,
        "hull": hull,
        "bbox": tuple(int(value) for value in bbox),
        "fractions": fractions,
        "runs": runs,
        "paper_brightness": median_v,
    }


def _document_quality(work, cluster):
    gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    x1, y1, x2, y2 = cluster["rect"]
    pad_x = max(3, int(0.06 * (x2 - x1)))
    pad_y = max(3, int(0.04 * (y2 - y1)))
    xa, xb = max(0, int(x1) - pad_x), min(w, int(x2) + pad_x)
    ya, yb = max(0, int(y1) - pad_y), min(h, int(y2) + pad_y)
    roi = gray[ya:yb, xa:xb]
    if roi.size == 0:
        return {
            "focus_ok": False, "lighting_ok": False,
            "too_dark": False, "glare": False,
            "band_lap_min": 0.0, "band_laps": [0.0, 0.0, 0.0],
            "line_lap_p20": 0.0, "light_median": 0.0,
            "light_tile_std": 999.0, "roi_rect": (xa, ya, xb, yb),
        }

    bands = [band for band in np.array_split(roi, 3, axis=0) if band.size]
    band_laps = [float(cv2.Laplacian(band, cv2.CV_64F).var()) for band in bands]
    line_laps = []
    for box in cluster["boxes"]:
        bx1, by1, bx2, by2 = box["bbox"]
        lx1, lx2 = max(0, int(bx1) - 2), min(w, int(bx2) + 3)
        ly1, ly2 = max(0, int(by1) - 2), min(h, int(by2) + 3)
        line_roi = gray[ly1:ly2, lx1:lx2]
        if line_roi.size:
            line_laps.append(float(cv2.Laplacian(
                line_roi, cv2.CV_64F).var()))
    line_lap_p20 = float(np.percentile(line_laps, 20)) if line_laps else 0.0

    tile_backgrounds = []
    for row_band in np.array_split(roi, 3, axis=0):
        for tile in np.array_split(row_band, 3, axis=1):
            if tile.size:
                tile_backgrounds.append(float(np.percentile(tile, 85)))
    light_median = float(np.median(tile_backgrounds))
    light_tile_std = float(np.std(tile_backgrounds))
    too_dark = light_median < CAPTURE_LIGHT_MIN
    glare_fraction = float(np.mean(roi >= 252))
    glare = (light_median > CAPTURE_LIGHT_MAX
             or (glare_fraction > 0.28 and light_tile_std > 18.0))
    focus_ok = (min(band_laps) >= CAPTURE_FOCUS_BAND_MIN
                and line_lap_p20 >= CAPTURE_LINE_FOCUS_MIN)
    lighting_ok = (not too_dark and not glare
                   and light_tile_std <= CAPTURE_LIGHT_TILE_STD_MAX)
    return {
        "focus_ok": focus_ok, "lighting_ok": lighting_ok,
        "too_dark": too_dark, "glare": glare,
        "band_lap_min": min(band_laps), "band_laps": band_laps,
        "line_lap_p20": line_lap_p20,
        "light_median": light_median,
        "light_tile_std": light_tile_std,
        "roi_rect": (xa, ya, xb, yb),
    }


def _outside_ocr_text_evidence(gray, text_rect):
    """Find dark text-like strokes that OCR missed below its last box.

    This deliberately inspects the original-resolution image.  Otherwise a
    soft final paragraph can disappear during the 960px detector resize and
    the focus ROI would stop above the exact area that needs checking.
    """
    h, w = gray.shape
    x1, y1, x2, y2 = [int(value) for value in text_rect]
    inset = max(3, int(round(0.04 * max(1, x2 - x1))))
    xa, xb = max(0, x1 + inset), min(w, x2 - inset)
    ya = min(h, y2 + 3)
    yb = min(h, y2 + int(round(0.24 * h)))
    band = gray[ya:yb, xa:xb]
    empty = {
        "height": int(band.shape[0]) if band.ndim == 2 else 0,
        "mean": 0.0,
        "fraction": 0.0,
        "focus_lap": 0.0,
        "last_text_y": int(y2),
        "present": False,
    }
    if band.size == 0 or min(band.shape) < 5:
        return empty

    # Black-hat isolates thin dark strokes on light paper without adding blur
    # to the OCR image.  Kernel dimensions scale with the captured text band.
    kernel_width = max(15, (band.shape[1] // 28) | 1)
    kernel_height = max(3, (int(round(h / 154.0))) | 1)
    blackhat = cv2.morphologyEx(
        band, cv2.MORPH_BLACKHAT,
        np.ones((kernel_height, kernel_width), np.uint8))
    mean_value = float(np.mean(blackhat))
    stroke_mask = blackhat >= 12
    stroke_fraction = float(np.mean(stroke_mask))
    focus_lap = float(cv2.Laplacian(band, cv2.CV_64F).var())
    present = (
        mean_value >= CAPTURE_OUTSIDE_TEXT_MEAN_MIN
        and stroke_fraction >= CAPTURE_OUTSIDE_TEXT_FRACTION_MIN
    )
    last_text_y = int(y2)
    if present:
        active_rows = np.flatnonzero(np.mean(stroke_mask, axis=1) >= 0.025)
        if len(active_rows):
            # Ignore short paper-edge/shadow runs at the end of the search
            # band. Real missed text occupies at least roughly two black-hat
            # kernel heights, even when it is only one printed line.
            runs = []
            run_start = run_end = int(active_rows[0])
            for row_index in active_rows[1:]:
                row_index = int(row_index)
                if row_index > run_end + 1:
                    runs.append((run_start, run_end))
                    run_start = row_index
                run_end = row_index
            runs.append((run_start, run_end))
            minimum_run = max(3, 2 * kernel_height)
            text_runs = [
                (start, end) for start, end in runs
                if end - start + 1 >= minimum_run
            ]
            if text_runs:
                last_text_y = min(h, ya + text_runs[-1][1] + 1)
    return {
        "height": int(band.shape[0]),
        "mean": mean_value,
        "fraction": stroke_fraction,
        "focus_lap": focus_lap,
        "last_text_y": last_text_y,
        "present": present,
    }


def analyze_capture_frame(frame, ocr_engine):
    """Analyze one immutable frame; all returned geometry belongs to it."""
    h_orig, w_orig = frame.shape[:2]
    scale = min(1.0, CAPTURE_ANALYSIS_WIDTH / float(w_orig))
    work = cv2.resize(
        frame, (int(round(w_orig * scale)), int(round(h_orig * scale))),
        interpolation=cv2.INTER_AREA)
    h, w = work.shape[:2]
    gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)
    global_lap = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    global_brightness = float(np.mean(gray))

    started = time.perf_counter()
    with OCR_LOCK:
        ocr_result = list(ocr_engine.predict(work))
    detection_seconds = time.perf_counter() - started
    all_boxes = _extract_detection_boxes(ocr_result, work)
    cluster = _dominant_document_cluster(all_boxes, w, h)

    base = {
        "analysis_scale": scale,
        "analysis_size": (w, h),
        "detection_seconds": detection_seconds,
        "all_box_count": len(all_boxes),
        "global_lap": global_lap,
        "global_brightness": global_brightness,
        "page_found": False,
        "page_complete": False,
        "content_complete": False,
        "dense_content_complete": False,
        "sparse_physical_complete": False,
        "physical_page_safe": False,
        "distance_ok": False,
        "text_readable": False,
        "missing_sides": [],
        "text_edge_sides": [],
        "physical_sides": [],
        "hard_text_edge_sides": [],
        "unreadable_sides": [],
        "outside_text_below": {
            "height": 0, "mean": 0.0, "fraction": 0.0,
            "focus_lap": 0.0, "last_text_y": 0, "present": False},
        "boxes": [],
        "outer_box": None,
        "paper": None,
        "row_count": 0,
        "focus_ok": global_lap >= 75.0,
        "lighting_ok": 45.0 <= global_brightness <= 215.0,
        "too_dark": global_brightness < 45.0,
        "glare": False,
        "band_lap_min": global_lap,
        "line_lap_p20": 0.0,
        "light_median": global_brightness,
        "light_tile_std": 0.0,
        "geometry_center": None,
        "geometry_area": 0.0,
        "geometry_angle": 0.0,
    }
    if cluster is None or len(cluster["boxes"]) < 2:
        return base

    quality = _document_quality(work, cluster)
    # The paper component remains useful telemetry, but it is deliberately not
    # part of capture eligibility or spoken direction in Box4c.
    paper = _strict_paper_component(work, cluster["rect"])
    x1, y1, x2, y2 = cluster["rect"]
    inv_scale = 1.0 / scale
    detected_rect_orig = (
        max(0, int(x1 * inv_scale)), max(0, int(y1 * inv_scale)),
        min(w_orig, int(x2 * inv_scale)), min(h_orig, int(y2 * inv_scale)),
    )
    original_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    outside_text_below = _outside_ocr_text_evidence(
        original_gray, detected_rect_orig)
    if outside_text_below["present"]:
        # The 960px live detector can miss a sharp final paragraph. Extend the
        # text envelope to the visible strokes instead of calling the page cut.
        y2 = min(
            float(h),
            max(y2, outside_text_below["last_text_y"] * scale))
    unreadable_sides = (
        ["B"] if (outside_text_below["present"]
                  and outside_text_below["focus_lap"]
                  < CAPTURE_EXTRA_TEXT_FOCUS_MIN) else [])

    median_height = max(1.0, cluster["median_height"])
    margin_pixels = {
        "L": max(0.0, x1), "T": max(0.0, y1),
        "R": max(0.0, w - x2), "B": max(0.0, h - y2),
    }
    text_margins = {
        "L": margin_pixels["L"] / w,
        "T": margin_pixels["T"] / h,
        "R": margin_pixels["R"] / w,
        "B": margin_pixels["B"] / h,
    }
    text_clearance_lines = {
        side: margin_pixels[side] / median_height for side in "LTRB"
    }
    text_edge_sides = [
        side for side in "LTRB"
        if (margin_pixels[side] <= 2.0
            or text_clearance_lines[side] < CAPTURE_TEXT_CLEARANCE_LINES)
    ]

    # Paper-edge contact is retained only so the recordings can compare it to
    # the text decision. It can never veto capture or generate a direction.
    physical_sides = []
    if paper is not None:
        for side in "LTRB":
            if (paper["fractions"][side] >= CAPTURE_PAPER_BORDER_FRACTION
                    and paper["runs"][side] >= CAPTURE_PAPER_BORDER_RUN):
                physical_sides.append(side)

    row_count = cluster["row_count"]
    page_found = len(cluster["boxes"]) >= 3
    text_envelope_complete = page_found and not text_edge_sides
    content_complete = text_envelope_complete
    page_complete = text_envelope_complete
    dense_content_complete = row_count > 12 and text_envelope_complete
    sparse_physical_complete = row_count <= 12 and text_envelope_complete
    physical_page_safe = paper is not None and not physical_sides
    missing_sides = list(text_edge_sides) if not text_envelope_complete else []

    geometry_width = max(1.0, x2 - x1)
    geometry_height = max(1.0, y2 - y1)
    cluster_width_ratio = geometry_width / w
    page_long_ratio = 0.0
    if paper is not None:
        _, _, page_width, page_height = paper["bbox"]
        page_long_ratio = max(page_width / w, page_height / h)
    median_line_ratio = median_height / h
    too_far = page_found and median_line_ratio < CAPTURE_MIN_LINE_HEIGHT_RATIO
    distance_ok = page_complete and not too_far

    rect_orig = (
        max(0, int(x1 * inv_scale)), max(0, int(y1 * inv_scale)),
        min(w_orig, int(x2 * inv_scale)), min(h_orig, int(y2 * inv_scale)),
    )

    boxes_orig = [
        (box["poly"] * inv_scale).astype(np.int32)
        for box in cluster["boxes"]
    ]
    paper_hull_orig = None
    if paper is not None:
        paper_hull_orig = (paper["hull"].astype(np.float32) * inv_scale).astype(np.int32)
    outer_box = {
        "rect": rect_orig,
        "touching_edge": not page_complete,
        "missing_sides": list(missing_sides),
        "paper_hull": paper_hull_orig,
        "text_margins": text_margins,
    }
    base.update(quality)
    base.update({
        "page_found": page_found,
        "page_complete": page_complete,
        "content_complete": content_complete,
        "text_envelope_complete": text_envelope_complete,
        "dense_content_complete": dense_content_complete,
        "sparse_physical_complete": sparse_physical_complete,
        "physical_page_safe": physical_page_safe,
        "distance_ok": distance_ok,
        "too_far": too_far,
        "text_readable": page_found and not unreadable_sides,
        "missing_sides": missing_sides,
        "text_edge_sides": text_edge_sides,
        "physical_sides": physical_sides,
        "hard_text_edge_sides": text_edge_sides,
        "unreadable_sides": unreadable_sides,
        "outside_text_below": outside_text_below,
        "boxes": boxes_orig,
        "outer_box": outer_box,
        "paper": paper,
        "row_count": row_count,
        "cluster_box_count": len(cluster["boxes"]),
        "cluster_width_ratio": cluster_width_ratio,
        "cluster_height_ratio": geometry_height / h,
        "cluster_background": cluster["background"],
        "max_gap_ratio": cluster["max_gap_ratio"],
        "median_line_ratio": median_line_ratio,
        "page_long_ratio": page_long_ratio,
        "text_margins": text_margins,
        "text_clearance_lines": text_clearance_lines,
        "geometry_center": ((x1 + x2) / (2.0 * w), (y1 + y2) / (2.0 * h)),
        "geometry_area": geometry_width * geometry_height / float(w * h),
        "geometry_angle": cluster["angle"],
    })
    return base


def detect_text_region_size(frame, ocr_engine):
    """Compatibility wrapper backed by Box4c's synchronized text analysis."""
    assessment = analyze_capture_frame(frame, ocr_engine)
    if assessment["missing_sides"]:
        hint = "further" if len(assessment["missing_sides"]) > 1 else "align"
    elif assessment.get("too_far"):
        hint = "closer"
    else:
        hint = None
    return (hint, assessment["boxes"], assessment["outer_box"],
            assessment["row_count"])


# ══════════════════════════════════════════════════════════════════════════════
#  NON-BLOCKING DETECTION WORKER & THREAD CONTAINER
# ══════════════════════════════════════════════════════════════════════════════
class DetectionResult:
    """Thread-safe storage for distinct, frame-synchronized analyses."""
    def __init__(self):
        self._lock = threading.Lock()
        self._payload = None
        self._generation = 0
        self._running = False

    def update(self, assessment, frame, sequence, frame_timestamp, error=None):
        with self._lock:
            self._generation += 1
            self._payload = {
                "generation": self._generation,
                "assessment": assessment,
                "frame": frame,
                "sequence": sequence,
                "frame_timestamp": frame_timestamp,
                "completed_at": time.monotonic(),
                "error": error,
            }
            self._running = False

    def get(self):
        with self._lock:
            return self._payload, self._running

    def set_running(self):
        with self._lock:
            if self._running:
                return False
            self._running = True
            return True

    def is_running(self):
        with self._lock:
            return self._running


def _detection_worker(frame, sequence, frame_timestamp, ocr_engine, det_result):
    try:
        assessment = analyze_capture_frame(frame, ocr_engine)
        det_result.update(
            assessment, frame, sequence, frame_timestamp, error=None)
    except Exception as exc:
        print(f"  [CAPTURE ANALYSIS] Failed: {exc}")
        det_result.update(
            None, frame, sequence, frame_timestamp, error=str(exc))


# ══════════════════════════════════════════════════════════════════════════════
#  QUALITY OVERLAY ON PREVIEW (with outer page box)
# ══════════════════════════════════════════════════════════════════════════════
def draw_quality_overlay(frame, assessment=None, stability_count=0):
    """Draw only a temporally credible document cluster and Box4c decisions."""
    display = frame.copy()
    h, w = display.shape[:2]
    geometry_reliable = bool(
        assessment is not None
        and assessment.get("geometry_reliable", True))
    if geometry_reliable:
        for poly in assessment.get("boxes", []):
            points = poly.reshape((-1, 1, 2)).astype(np.int32)
            cv2.polylines(
                display, [points], isClosed=True,
                color=(255, 255, 0), thickness=2)
        outer = assessment.get("outer_box")
        if outer is not None:
            x1, y1, x2, y2 = outer["rect"]
            text_ok = bool(assessment.get("text_envelope_complete"))
            box_color = (0, 220, 0) if text_ok else (0, 0, 230)
            sides = "".join(assessment.get("missing_sides", []))
            unreadable = "".join(assessment.get("unreadable_sides", []))
            label = "TEXT SAFE" if text_ok else (
                f"TEXT SOFT: {unreadable}" if unreadable else
                f"TEXT EDGE: {sides}" if sides else "TEXT NOT CONFIRMED")
            cv2.rectangle(display, (x1, y1), (x2, y2), box_color, 3)
            cv2.putText(
                display, label, (x1 + 5, max(y1 - 8, 20)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.58, box_color, 2, cv2.LINE_AA)

    bar_h = 68
    overlay = display.copy()
    cv2.rectangle(overlay, (0, 0), (w, bar_h), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.6, display, 0.4, 0, display)
    if assessment is None:
        status = "Analyzing page ..."
        details = "Waiting for a fresh OCR result"
        color = (0, 200, 255)
    else:
        critical_ok = all((
            assessment.get("analysis_fresh", True),
            assessment.get("page_complete", False),
            assessment.get("temporal_coverage_ok", True),
            assessment.get("distance_ok", False),
            assessment.get("focus_ok", False),
            assessment.get("lighting_ok", False),
            assessment.get("motion_ok", False),
        ))
        color = (0, 220, 0) if critical_ok else (0, 0, 230)
        status = assessment.get("guidance_text", "Checking page")
        reference_rows = (assessment.get("coverage_reference_rows")
                          or assessment.get("row_count", 0))
        details = (
            f"Rows:{assessment.get('row_count', 0)}/{reference_rows:.0f}  "
            f"BandLap:{assessment.get('band_lap_min', 0):.0f}  "
            f"LineLap:{assessment.get('line_lap_p20', 0):.0f}  "
            f"LightStd:{assessment.get('light_tile_std', 0):.1f}  "
            f"Stable:{stability_count}/{CAPTURE_REQUIRED_DISTINCT}")
    cv2.putText(
        display, status, (12, 27), cv2.FONT_HERSHEY_SIMPLEX,
        0.62, color, 2, cv2.LINE_AA)
    cv2.putText(
        display, details, (12, 55), cv2.FONT_HERSHEY_SIMPLEX,
        0.46, (235, 235, 235), 1, cv2.LINE_AA)
    return display


# ══════════════════════════════════════════════════════════════════════════════
#  PRE-CAPTURE QUALITY LOOP
# ══════════════════════════════════════════════════════════════════════════════
def _estimate_capture_motion(previous_payload, current_payload):
    """Measure camera/page movement between two independently analyzed frames."""
    if previous_payload is None:
        return {"known": False, "ok": True, "score": 0.0,
                "shift": 0.0, "geometry_shift": 0.0}
    previous = previous_payload["frame"]
    current = current_payload["frame"]
    previous_gray = cv2.cvtColor(
        cv2.resize(previous, (320, 180), interpolation=cv2.INTER_AREA),
        cv2.COLOR_BGR2GRAY).astype(np.float32)
    current_gray = cv2.cvtColor(
        cv2.resize(current, (320, 180), interpolation=cv2.INTER_AREA),
        cv2.COLOR_BGR2GRAY).astype(np.float32)
    try:
        (shift_x, shift_y), response = cv2.phaseCorrelate(
            previous_gray, current_gray)
        shift_ratio = float(np.hypot(shift_x / 320.0, shift_y / 180.0))
        if response < 0.04:
            shift_ratio = max(
                shift_ratio,
                float(np.mean(np.abs(previous_gray - current_gray))) / 255.0)
    except cv2.error:
        shift_ratio = float(np.mean(
            np.abs(previous_gray - current_gray))) / 255.0

    previous_assessment = previous_payload.get("assessment") or {}
    current_assessment = current_payload.get("assessment") or {}
    previous_center = previous_assessment.get("geometry_center")
    current_center = current_assessment.get("geometry_center")
    geometry_shift = 0.0
    area_change = 0.0
    angle_change = 0.0
    if previous_center is not None and current_center is not None:
        geometry_shift = float(np.hypot(
            previous_center[0] - current_center[0],
            previous_center[1] - current_center[1]))
        previous_area = max(previous_assessment.get("geometry_area", 0.0), 1e-6)
        current_area = max(current_assessment.get("geometry_area", 0.0), 1e-6)
        area_change = abs(current_area / previous_area - 1.0)
        angle_change = abs(
            current_assessment.get("geometry_angle", 0.0)
            - previous_assessment.get("geometry_angle", 0.0))
    diagnostic_score = max(
        shift_ratio / CAPTURE_MOTION_SHIFT_MAX,
        geometry_shift / CAPTURE_MOTION_GEOMETRY_MAX,
        area_change / CAPTURE_MOTION_AREA_CHANGE_MAX,
        angle_change / CAPTURE_MOTION_ANGLE_MAX,
    )
    # OCR polygons appear/disappear between otherwise sharp frames. Their
    # centre/area/angle remain logged, but only direct image motion controls the
    # gate; actual motion blur is independently rejected by the focus checks.
    score = shift_ratio / CAPTURE_MOTION_SHIFT_MAX
    return {
        "known": True,
        "ok": shift_ratio <= CAPTURE_MOTION_SHIFT_MAX,
        "score": float(score),
        "diagnostic_score": float(diagnostic_score),
        "shift": shift_ratio,
        "geometry_shift": geometry_shift,
        "area_change": area_change,
        "angle_change": angle_change,
    }


def _guidance_for_assessment(assessment):
    """Choose one truthful instruction using explicit failure-state priority."""
    motion_known = assessment.get("motion_known", False)
    motion_ok = assessment.get("motion_ok", True)
    if not assessment.get("analysis_fresh", True):
        return "checking"
    if not assessment.get("page_found", False):
        if assessment.get("too_dark", False):
            return "too_dark"
        if assessment.get("glare", False):
            return "glare"
        if (assessment.get("all_box_count", 0) >= 2
                and not assessment.get("focus_ok", False)):
            return "blurry"
        if motion_known and not motion_ok:
            return "hold_still"
        return "page_not_found"

    if assessment.get("too_dark", False):
        return "too_dark"
    if assessment.get("glare", False):
        return "glare"
    if not assessment.get("lighting_ok", False):
        return "bad_lighting"
    if (assessment.get("unreadable_sides")
            or not assessment.get("focus_ok", False)):
        return "blurry"
    if motion_known and not motion_ok:
        return "hold_still"
    if not assessment.get("page_complete", False):
        # Missing sides come only from the text envelope in Box4c. Physical A4
        # borders and an unknown classifier result can never generate a turn.
        sides = assessment.get("missing_sides", [])
        if len(sides) > 1:
            return "move_back"
        if sides:
            return {
                "T": "look_up", "B": "look_down",
                "L": "look_left", "R": "look_right",
            }[sides[0]]
        return "page_not_found"
    if not assessment.get("temporal_coverage_ok", True):
        return "hold_still"
    if not assessment.get("distance_ok", False):
        return "move_closer"
    return "almost_ready"


def _capture_gate_passes(assessment, already_stable=False):
    """Independent hard gates; lighting can never compensate for bad focus."""
    focus_ok = assessment.get("focus_ok", False)
    lighting_ok = assessment.get("lighting_ok", False)
    if already_stable:
        # Small hysteresis prevents a one-point threshold flutter while still
        # requiring every accepted observation to be safely usable.
        focus_ok = (
            assessment.get("band_lap_min", 0.0) >= 0.88 * CAPTURE_FOCUS_BAND_MIN
            and assessment.get("line_lap_p20", 0.0) >= 0.88 * CAPTURE_LINE_FOCUS_MIN)
        lighting_ok = (
            not assessment.get("too_dark", False)
            and not assessment.get("glare", False)
            and assessment.get("light_tile_std", 999.0)
            <= CAPTURE_LIGHT_TILE_STD_MAX + 3.0)
    return all((
        assessment.get("analysis_fresh", True),
        assessment.get("page_complete", False),
        assessment.get("temporal_coverage_ok", True),
        assessment.get("distance_ok", False),
        assessment.get("text_readable", False),
        focus_ok,
        lighting_ok,
        assessment.get("motion_ok", True),
    ))


def pre_capture_quality_loop(frame_holder, ocr_engine, tts_module,
                             key_queue, event_queue, debug_recorder=None):
    """Guide a head-mounted camera and capture three distinct safe frames."""
    _gui_emit("stage", stage="capture", status="Analyzing page and focus")
    print("\n[QUALITY] Box6 text-envelope capture guidance active …")
    print("[QUALITY] Checking all printed text, focus, lighting, and image motion.")
    print("[QUALITY] Three good frames in the latest four are required. S force-captures.")

    guidance = None
    try:
        guidance = AudioGuidance(
            tts_module, state_cooldown=4.5, global_cooldown=0.80,
            event_callback=(
                lambda event_type, **details: _safe_debug_call(
                    debug_recorder, "record_event", event_type, **details)
                if debug_recorder is not None else None))
    except Exception as exc:
        print(f"[GUIDANCE] Spoken guidance unavailable: {exc}")

    detection_result = DetectionResult()
    last_analysis_started = 0.0
    last_started_sequence = -1
    last_processed_generation = 0
    previous_payload = None
    latest_assessment = None
    latest_assessment_timestamp = 0.0
    stability_window = []
    stable_payloads = []
    coverage_history = []
    pending_guidance = None
    pending_guidance_count = 0
    entered_at = time.monotonic()
    stale_warning_at = 0.0

    try:
        while True:
            action = capture_action(key_queue, event_queue)
            if action in ('quit', 'disconnect', 'cancel'):
                _safe_debug_call(debug_recorder, 'record_event',
                                 'capture_cancelled', source=action)
                return None
            if _GUI_CONTROLLER is not None and _GUI_CONTROLLER.stop_requested.is_set():
                return None
            frame, sequence, frame_timestamp = frame_holder.get_snapshot()
            now = time.monotonic()
            age = now - (frame_timestamp if frame is not None else entered_at)
            if frame is None or age > STREAM_STALE_SECONDS:
                if age > STREAM_TIMEOUT_SECONDS:
                    if event_queue is not None:
                        event_queue.put({"event": "disconnect", "error": "Camera frames stalled"})
                    return None
                if now - stale_warning_at > 2.0:
                    _gui_emit("status", message="Waiting for fresh camera frames")
                    stale_warning_at = now
                time.sleep(0.05)
                continue
            if action == "force":
                _safe_debug_call(debug_recorder, "record_event", "forced_capture",
                                 source="control", frame_sequence=sequence)
                beep_ready()
                return frame

            if (sequence != last_started_sequence
                    and now - last_analysis_started >= CAPTURE_ANALYSIS_INTERVAL
                    and detection_result.set_running()):
                last_started_sequence = sequence
                last_analysis_started = now
                threading.Thread(
                    target=_detection_worker,
                    args=(frame, sequence, frame_timestamp,
                          ocr_engine, detection_result),
                    daemon=True).start()

            payload, _ = detection_result.get()
            if (payload is not None
                    and payload["generation"] != last_processed_generation):
                last_processed_generation = payload["generation"]
                assessment = payload.get("assessment")
                passed = False
                motion = {}
                allow_guidance = False
                if assessment is None:
                    latest_assessment = None
                    latest_assessment_timestamp = 0.0
                    stability_window = []
                    stable_payloads = []
                    state = "page_not_found"
                    print(f"  [ANALYSIS] Error: {payload.get('error')}")
                    _safe_debug_call(
                        debug_recorder, "record_event",
                        "capture_analysis_failed",
                        generation=payload.get("generation"),
                        frame_sequence=payload.get("sequence"),
                        error=payload.get("error"))
                else:
                    analysis_age = max(
                        0.0, time.monotonic() - payload["frame_timestamp"])
                    analysis_fresh = analysis_age <= CAPTURE_MAX_ANALYSIS_AGE
                    assessment["analysis_age"] = analysis_age
                    assessment["analysis_fresh"] = analysis_fresh

                    if (analysis_fresh
                            and assessment.get("page_found", False)
                            and assessment.get("focus_ok", False)
                            and assessment.get("lighting_ok", False)):
                        coverage_history.append(
                            float(assessment.get("row_count", 0)))
                        coverage_history = coverage_history[-CAPTURE_COVERAGE_HISTORY:]
                    reference_rows = (
                        float(np.percentile(coverage_history, 90))
                        if coverage_history else
                        float(assessment.get("row_count", 0)))
                    temporal_coverage_ok = (
                        not coverage_history
                        or assessment.get("row_count", 0)
                        >= CAPTURE_COVERAGE_RATIO * max(reference_rows, 1.0)
                    )
                    assessment["coverage_reference_rows"] = reference_rows
                    assessment["temporal_coverage_ok"] = temporal_coverage_ok
                    assessment["geometry_reliable"] = temporal_coverage_ok

                    motion = _estimate_capture_motion(previous_payload, payload)
                    assessment["motion_known"] = motion["known"]
                    assessment["motion_ok"] = motion["ok"]
                    assessment["motion_score"] = motion["score"]
                    state = _guidance_for_assessment(assessment)
                    assessment["guidance_state"] = state
                    assessment["guidance_text"] = GUIDANCE_PROMPTS[state]
                    latest_assessment = assessment if analysis_fresh else None
                    latest_assessment_timestamp = (
                        payload["frame_timestamp"] if analysis_fresh else 0.0)
                    allow_guidance = analysis_fresh and state != "checking"

                    passed = _capture_gate_passes(
                        assessment, already_stable=bool(stable_payloads))
                    hard_failure = bool(
                        analysis_fresh and (
                            not assessment.get("page_found", False)
                            or assessment.get("missing_sides")
                            or assessment.get("unreadable_sides")))
                    previous_pass_count = len(stable_payloads)
                    if hard_failure:
                        stability_window = []
                    else:
                        stability_window.append((payload, passed))
                        stability_window = stability_window[-CAPTURE_STABILITY_WINDOW:]
                    stable_payloads = [
                        item for item, item_passed in stability_window
                        if item_passed]
                    if (not passed and previous_pass_count
                            and not hard_failure):
                        print(
                            "  [STABLE HOLD] One soft failure retained; "
                            f"{len(stable_payloads)}/{CAPTURE_REQUIRED_DISTINCT} "
                            "good frames remain.")
                    elif hard_failure and previous_pass_count:
                        print(
                            "  [STABLE HOLD] Cleared by a confirmed text-edge "
                            "or unreadable-text failure.")

                    sides = "".join(assessment.get("missing_sides", [])) or "none"
                    print(
                        f"  [OBS {payload['generation']:03d}] "
                        f"state={state}, rows={assessment.get('row_count', 0)}/"
                        f"{reference_rows:.0f}, age={analysis_age:.2f}s, "
                        f"edges={sides}, bandLap={assessment.get('band_lap_min', 0):.0f}, "
                        f"lineLap={assessment.get('line_lap_p20', 0):.0f}, "
                        f"lightStd={assessment.get('light_tile_std', 0):.1f}, "
                        f"belowText={assessment.get('outside_text_below', {}).get('mean', 0):.1f}, "
                        f"motion={assessment.get('motion_score', 0):.2f}, "
                        f"stable={len(stable_payloads)}/{CAPTURE_REQUIRED_DISTINCT}")

                    _safe_debug_call(
                        debug_recorder, "record_analysis",
                        payload, assessment, state, passed,
                        len(stable_payloads), motion=motion)
                    _gui_emit(
                        "quality_metrics", assessment=dict(assessment),
                        stable_count=len(stable_payloads),
                        required=CAPTURE_REQUIRED_DISTINCT,
                        passed=passed)

                    if (passed
                            and len(stable_payloads) >= CAPTURE_REQUIRED_DISTINCT
                            and (stable_payloads[-1]["completed_at"]
                                 - stable_payloads[0]["completed_at"]
                                 >= CAPTURE_MIN_STABLE_SECONDS)):
                        chosen = max(
                            stable_payloads,
                            key=lambda item: (
                                item["assessment"].get("row_count", 0),
                                item["assessment"].get("cluster_box_count", 0),
                                item["assessment"].get("band_lap_min", 0.0)
                                + 0.25 * item["assessment"].get(
                                    "line_lap_p20", 0.0),
                                item.get("completed_at", 0.0)))
                        print(
                            "\n[QUALITY] ✅ Three of the latest four observations passed; "
                            f"capturing analyzed frame #{chosen['sequence']}.")
                        _safe_debug_call(
                            debug_recorder, "record_event",
                            "automatic_capture",
                            generation=chosen.get("generation"),
                            frame_sequence=chosen.get("sequence"),
                            stability_count=len(stable_payloads))
                        beep_ready()
                        return chosen["frame"]
                    if analysis_fresh:
                        previous_payload = payload

                if allow_guidance:
                    if state == pending_guidance:
                        pending_guidance_count += 1
                    else:
                        pending_guidance = state
                        pending_guidance_count = 1
                    confirmation_count = 2
                    if (guidance is not None
                            and pending_guidance_count >= confirmation_count):
                        guidance.notify(state)

            display_assessment = latest_assessment
            if (latest_assessment_timestamp <= 0.0
                    or time.monotonic() - latest_assessment_timestamp
                    > CAPTURE_MAX_ANALYSIS_AGE):
                # Never draw old boxes/directions on a newer live pose.
                display_assessment = None
            display = draw_quality_overlay(
                frame, display_assessment, len(stable_payloads))
            _display_frame(
                "Smart Glasses Live Stream", display, cli_size=(640, 360))

            time.sleep(0.025)
    finally:
        if guidance is not None:
            guidance.stop()


# ══════════════════════════════════════════════════════════════════════════════
#  PROCESS & SPEAK
# ══════════════════════════════════════════════════════════════════════════════

def _processing_cancelled(key_queue, event_queue):
    if _GUI_CONTROLLER is not None and _GUI_CONTROLLER.stop_requested.is_set():
        return True
    actions = pending_actions(key_queue, event_queue)
    cancelled = any(action in ("quit", "disconnect", "stop", "capture") for action in actions)
    if cancelled:
        print("[CONTROL] Current document cancelled.")
        _gui_emit("stage", stage="camera", status="Document cancelled; ready")
    return cancelled


def _save_run_evidence(run_count, img_path, paragraphs, timings):
    """Machine-readable OCR-to-translation mapping, before playback can fail."""
    record = {
        "script": "pipeline_cli_box7.py",
        "run": run_count, "image": os.path.abspath(img_path),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "timings": timings, "regions": paragraphs,
    }
    path = os.path.join(PARAGRAPH_TEST_DIR,
                        os.path.splitext(os.path.basename(img_path))[0] + "_box7.json")
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(DebugSessionRecorder._json_safe(record), handle,
                      ensure_ascii=False, indent=2)
    except OSError as exc:
        print(f"[DEBUG] Could not save run evidence: {exc}")


def _audio_event(event_type, **payload):
    _safe_debug_call(_ACTIVE_DEBUG_RECORDER, "record_event", event_type, **payload)
    controller = _GUI_CONTROLLER
    if controller is None:
        if event_type in ("audio_error", "audio_route"):
            print(f"[AUDIO] {payload.get('message', event_type)}")
        return
    if event_type == "pi_control" and controller.pipeline_event_queue is not None:
        controller.pipeline_event_queue.put({"cmd": "tts_control", "action": payload.get("action")})
    elif event_type == "audio_route":
        controller.publish_event("audio_route", **payload)
        if (payload.get("route_changed") and _PLAYBACK_ACTIVE.is_set()
                and controller.pipeline_event_queue is not None):
            controller.pipeline_event_queue.put({"cmd": "audio_route_changed"})
    elif event_type == "audio_error":
        controller.publish_event("status", message=payload.get("message", "Audio unavailable"))


def _close_socket(sock):
    if sock is None:
        return
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass


def _announce_status(tts, message):
    # A Pi connection cannot play startup speech before the Pi client connects.
    if AUDIO_ROUTER is not None and AUDIO_ROUTER.pi_enabled and not AUDIO_ROUTER.connected:
        print(f"[STATUS] {message}")
        return
    try:
        tts.speak(message)
        tts.wait_until_done()
    except RuntimeError as exc:
        print(f"[AUDIO STATUS] {exc}")
        _gui_emit("status", message=str(exc))


def _replay_last_document(action, key_queue, event_queue):
    if not _LAST_DOCUMENT:
        return
    index = _LAST_DOCUMENT["index"]
    if action == "next":
        index = min(index + 1, len(_LAST_DOCUMENT["paragraphs"]) - 1)
    elif action == "previous":
        index = max(0, index - 1)
    run_tts_paragraphs(
        _LAST_DOCUMENT["tts"], _LAST_DOCUMENT["paragraphs"], _LAST_DOCUMENT["image"],
        key_queue, event_queue, _LAST_DOCUMENT["output_language"], start_index=index)


def process_and_speak(frame, run_count, language, book_mode,
                      ocr_engine, unwarper, translator, tokenizer, tts_module,
                      key_queue=None, event_queue=None,
                      output_language="english", document_tts_module=None):
    print(f"\n{'─'*60}")
    print(f"  RUN #{run_count}")
    print(f"{'─'*60}\n")

    pipeline_t0 = time.time()
    if _processing_cancelled(key_queue, event_queue):
        return False
    beep_capture()
    img_path = save_frame(frame, run_count)

    # Score the actual JPEG that OCR would consume, not only the live frame.
    saved_frame = cv2.imread(img_path)
    if saved_frame is None:
        print("[POST-CAPTURE] Could not reload saved image; retry requested.")
        beep_failure()
        return False
    _, post_details = score_frame_quality(saved_frame)
    print(
        f"[POST-CAPTURE] LapVar={post_details['sharpness']:.1f}, "
        f"score components: sharp={post_details['sharpness_pts']}, "
        f"bright={post_details['brightness_pts']}, "
        f"even={post_details['evenness_pts']}")
    if post_details["sharpness"] < POST_CAPTURE_LAP_MIN:
        print(
            "[POST-CAPTURE] Rejected: saved frame is too soft for reliable "
            f"OCR ({post_details['sharpness']:.1f} < {POST_CAPTURE_LAP_MIN:.0f}).")
        beep_failure()
        return False

    _display_frame(
        "Captured Image (OCR Target)", frame, cli_size=(640, 360))

    # Stage 1: OCR with line coordinates and paragraph grouping.
    _gui_emit("stage", stage="ocr", status="Running OCR")
    extracted_text, paragraphs, ocr_image, t_ocr = run_ocr(
        ocr_engine, img_path, book_mode, unwarper)
    if _processing_cancelled(key_queue, event_queue):
        return False
    if not extracted_text or not paragraphs:
        tone_failure()
        tts_module.speak("No text detected. Please try again.")
        tts_module.wait_until_done()
        print("[WARN] No text extracted. Skipping.\n")
        return False

    tone_success()
    if ocr_image is None:
        ocr_image = saved_frame
    show_paragraph_preview(ocr_image, paragraphs, active_index=None)

    print(f"\n{'='*60}")
    print("  EXTRACTED TEXT")
    print(f"{'='*60}")
    print(extracted_text)
    print(f"{'='*60}\n")

    counts = _region_counts(paragraphs)
    print(
        f"[REGIONS] Detected {len(paragraphs)} text region(s): "
        f"{counts['titles']} title, {counts['headings']} heading(s), "
        f"{counts['paragraphs']} paragraph(s)")
    for paragraph in paragraphs:
        location = paragraph_page_location(paragraph, ocr_image.shape)
        print(
            f"  {_region_label(paragraph)} | "
            f"type={paragraph.get('region_type', 'paragraph')} | "
            f"{len(paragraph['lines'])} line(s) | "
            f"confidence={paragraph['confidence']:.3f} | {location}")
        print(f"    {paragraph['source_text']}")
    _gui_emit(
        "ocr_result", text=extracted_text,
        paragraphs=[dict(paragraph) for paragraph in paragraphs],
        elapsed=t_ocr)

    # Stage 2: translate all paragraph chunks in one GPU batch, retaining IDs.
    _gui_emit("stage", stage="translation", status="Translating document")
    translated_paragraphs, t_translate = (
        run_translation_nllb_paragraphs_for_output(
            paragraphs, language, output_language, translator, tokenizer))
    if _processing_cancelled(key_queue, event_queue):
        return False
    translated_output_text = " ".join(
        paragraph["translated_text"] for paragraph in translated_paragraphs)

    translation_was_used = any(
        paragraph.get("translation_status") == "translated"
        for paragraph in translated_paragraphs)
    if translation_was_used:
        print(f"\n{'='*60}")
        print(
            f"  CLASSIFIED REGION TRANSLATIONS "
            f"({output_language.title()}) — NLLB 1.3B")
        print(f"{'='*60}")
        for paragraph in translated_paragraphs:
            print(
                f"[{_region_label(paragraph)}] "
                f"{paragraph['translated_text']}")
        print(f"{'='*60}\n")
    _gui_emit(
        "translation_result", text=translated_output_text,
        output_language=output_language, elapsed=t_translate)

    # Stage 2.5: apply the existing conservative SymSpell rules separately.
    t_spell = 0.0
    all_spell_changes = []
    spell_t0 = time.time()
    for paragraph in translated_paragraphs:
        translated_text = paragraph["translated_text"]
        if SPELL_CORRECTOR is not None and output_language == "english":
            corrected_text, changes = run_spell_correction(
                SPELL_CORRECTOR, translated_text)
        else:
            corrected_text, changes = translated_text, []
        paragraph["spoken_text"] = corrected_text
        paragraph["spell_changes"] = changes
        for original, replacement in changes:
            all_spell_changes.append(
                (_region_label(paragraph), original, replacement))
    t_spell = (
        time.time() - spell_t0
        if SPELL_CORRECTOR is not None and output_language == "english"
        else 0.0)
    corrected_text = " ".join(
        paragraph["spoken_text"] for paragraph in translated_paragraphs)

    if SPELL_CORRECTOR is not None and output_language == "english":
        if all_spell_changes:
            print(
                f"[STAGE 2.5] Applied {len(all_spell_changes)} conservative "
                f"correction(s) in {t_spell:.3f}s:")
            for region_label, original, replacement in all_spell_changes:
                print(
                    f"  {region_label}: {original} -> {replacement}")
        else:
            print(
                f"[STAGE 2.5] No sufficiently confident corrections; "
                f"original preserved ({t_spell:.3f}s)")

    # DEBUG — remove when done
    save_run_debug_outputs(
        run_count, img_path, ocr_image, translated_paragraphs,
        extracted_text, language, all_spell_changes, output_language)

    # Save both full-page text and the source-to-audio paragraph mapping.
    try:
        log_dir = os.path.join(PROJECT_DIR, "1Pipeline", "final", "working")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "ocr_results_comparison.txt")
        with open(log_path, "a", encoding="utf-8") as f_log:
            f_log.write("="*80 + "\n")
            f_log.write(f"TIMESTAMP   : {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f_log.write("CODE VERSION: pipeline_cli_box6.py\n")
            f_log.write(f"RUN NUMBER  : {run_count}\n")
            f_log.write(f"IMAGE FILE  : {os.path.basename(img_path)}\n")
            log_counts = _region_counts(translated_paragraphs)
            f_log.write(f"TEXT REGIONS: {len(translated_paragraphs)}\n")
            f_log.write(f"PAGE TITLES : {log_counts['titles']}\n")
            f_log.write(f"HEADINGS    : {log_counts['headings']}\n")
            f_log.write(f"PARAGRAPHS  : {log_counts['paragraphs']}\n")
            f_log.write("-"*80 + "\n")
            f_log.write("EXTRACTED TEXT:\n")
            f_log.write(extracted_text + "\n")
            if translation_was_used:
                f_log.write("-"*80 + "\n")
                f_log.write(
                    f"TRANSLATED TEXT ({output_language.upper()}):\n")
                f_log.write(translated_output_text + "\n")
            if all_spell_changes:
                f_log.write("-"*80 + "\n")
                f_log.write("CONSERVATIVELY CORRECTED TEXT:\n")
                f_log.write(corrected_text + "\n")

            for paragraph in translated_paragraphs:
                f_log.write("-"*80 + "\n")
                f_log.write(
                    f"REGION {_region_label(paragraph)} | "
                    f"ID={paragraph.get('region_id', paragraph['number'])} | "
                    f"TYPE={paragraph.get('region_type', 'paragraph')} | "
                    f"BBOX={paragraph['bbox']} | "
                    f"CONFIDENCE={paragraph['confidence']:.3f}\n")
                f_log.write(
                    f"SOURCE     : {paragraph['source_text']}\n")
                if translation_was_used:
                    f_log.write(
                        f"TRANSLATED : {paragraph['translated_text']}\n")
                f_log.write(
                    f"SPOKEN     : {paragraph['spoken_text']}\n")
                if paragraph["spell_changes"]:
                    f_log.write(
                        f"CORRECTIONS: {paragraph['spell_changes']}\n")
            f_log.write("="*80 + "\n\n")
    except Exception as log_error:
        print(f"[LOG ERROR] Failed to save OCR output to log: {log_error}")

    # Stage 3: each spoken title/heading/paragraph stays tied to its source box.
    processing_before_audio = time.time() - pipeline_t0
    _save_run_evidence(run_count, img_path, translated_paragraphs,
                       {"ocr": t_ocr, "translation": t_translate,
                        "spell": t_spell, "processing_before_audio": processing_before_audio})
    if _processing_cancelled(key_queue, event_queue):
        return False
    selected_document_tts = document_tts_module or tts_module
    global _LAST_DOCUMENT
    _LAST_DOCUMENT = {"tts": selected_document_tts, "paragraphs": translated_paragraphs,
                      "image": ocr_image, "output_language": output_language, "index": 0,
                      "status_tts": tts_module}
    t_tts = run_tts_paragraphs(
        selected_document_tts, translated_paragraphs, ocr_image,
        key_queue, event_queue, output_language=output_language)

    total_pipeline = time.time() - pipeline_t0
    print(f"\n{'─'*40}")
    print("  ⏱  TIMING BREAKDOWN")
    print(f"{'─'*40}")
    print(f"  OCR          : {t_ocr:.3f}s")
    if translation_was_used:
        print(f"  Translation  : {t_translate:.3f}s")
    else:
        print("  Translation  : skipped")
    print(f"  Spell Corr   : {t_spell:.3f}s")
    print(f"  TTS Playback : {t_tts:.3f}s")
    print(f"{'─'*40}")
    print(f"  TOTAL        : {total_pipeline:.3f}s")
    print(f"{'─'*40}")
    print("\n  Ready for next capture!\n")
    average_confidence = (
        sum(float(p.get("confidence", 0.0)) for p in translated_paragraphs)
        / max(1, len(translated_paragraphs)))
    _gui_emit(
        "run_complete", run_count=run_count,
        source_language=language,
        ocr_text=extracted_text,
        translated_text=translated_output_text,
        output_language=output_language,
        paragraphs=[dict(paragraph) for paragraph in translated_paragraphs],
        average_confidence=average_confidence,
        timings={
            "ocr": t_ocr, "translation": t_translate,
            "spell": t_spell, "tts": t_tts,
            "total": total_pipeline,
            "processing_before_audio": processing_before_audio,
        })
    _gui_emit("stage", stage="camera", status="Ready for next capture")
    return True


# ══════════════════════════════════════════════════════════════════════════════
#  CLI HELPERS
# ══════════════════════════════════════════════════════════════════════════════
BANNER = r"""
============================================================
FYDP SMART GLASSES - BOX6
Local OCR -> NLLB translation -> paragraph-linked Piper audio

S: start capture / force capture while guiding / stop speech
A: pause or resume speech
R: repeat region   N: next region   P: previous region
Q: quit

Text-envelope guidance; three good observations in the latest
four are required. PC audio by default; --audio-pi selects Pi.
============================================================
"""


class SuppressOutput:
    def __enter__(self):
        self._stdout = sys.stdout
        self._stderr = sys.stderr
        sys.stdout = open(os.devnull, 'w')
        sys.stderr = open(os.devnull, 'w')

    def __exit__(self, exc_type, exc_val, exc_tb):
        sys.stdout.close()
        sys.stdout = self._stdout
        sys.stderr = self._stderr


def ask_language():
    print("\n── Select source language ──")
    print("  1) French")
    print("  2) Chinese")
    print("  3) Spanish")
    print("  4) English (no translation, OCR + TTS only)")
    while True:
        choice = input("\nEnter 1/2/3/4: ").strip()
        if choice == "1": return "french"
        if choice == "2": return "chinese"
        if choice == "3": return "spanish"
        if choice == "4": return "english"
        print("[!] Invalid choice.")


def ask_book_mode():
    print("\n── Book Mode (curved page unwarping) ──")
    while True:
        choice = input("Enable book mode? (y/n): ").strip().lower()
        if choice in ("y", "yes"): return True
        if choice in ("n", "no"):  return False
        print("[!] Enter y or n.")


# =============================================================================
# BOX5 DESKTOP GUI AND EVALUATION HELPERS
# =============================================================================
def _normalise_eval_text(text):
    text = unicodedata.normalize("NFC", text or "")
    return re.sub(r"\s+", " ", text).strip()


def _edit_counts(reference_units, hypothesis_units):
    """Return Levenshtein distance plus substitutions/deletions/insertions."""
    rows = len(reference_units) + 1
    cols = len(hypothesis_units) + 1
    # Two cost rows plus one direction byte per cell avoid a very large Python
    # integer matrix when evaluating a complete A4 page.
    directions = bytearray(rows * cols)
    for j in range(1, cols):
        directions[j] = 3  # insertion
    for i in range(1, rows):
        directions[i * cols] = 2  # deletion
    previous = list(range(cols))
    for i in range(1, rows):
        current = [i] + [0] * (cols - 1)
        for j in range(1, cols):
            if reference_units[i - 1] == hypothesis_units[j - 1]:
                current[j] = previous[j - 1]
                directions[i * cols + j] = 0
                continue
            substitution = previous[j - 1] + 1
            deletion = previous[j] + 1
            insertion = current[j - 1] + 1
            best = min(substitution, deletion, insertion)
            current[j] = best
            if substitution == best:
                directions[i * cols + j] = 1
            elif deletion == best:
                directions[i * cols + j] = 2
            else:
                directions[i * cols + j] = 3
        previous = current

    i, j = len(reference_units), len(hypothesis_units)
    substitutions = deletions = insertions = 0
    while i > 0 or j > 0:
        direction = directions[i * cols + j]
        if direction == 0:
            i -= 1
            j -= 1
        elif direction == 1:
            substitutions += 1
            i -= 1
            j -= 1
        elif direction == 2:
            deletions += 1
            i -= 1
        else:
            insertions += 1
            j -= 1
    return {
        "distance": previous[-1],
        "substitutions": substitutions,
        "deletions": deletions,
        "insertions": insertions,
    }


def calculate_text_metrics(reference, hypothesis, calculate_wer=True):
    """Calculate Unicode-normalised CER/WER without external dependencies."""
    reference = _normalise_eval_text(reference)
    hypothesis = _normalise_eval_text(hypothesis)
    if not reference:
        return {
            "available": False,
            "reason": "Ground truth is empty",
            "reference": reference,
            "hypothesis": hypothesis,
        }

    char_counts = _edit_counts(list(reference), list(hypothesis))
    reference_words = reference.split()
    hypothesis_words = hypothesis.split()
    word_counts = (
        _edit_counts(reference_words, hypothesis_words)
        if calculate_wer else None)
    return {
        "available": True,
        "reference": reference,
        "hypothesis": hypothesis,
        "reference_characters": len(reference),
        "hypothesis_characters": len(hypothesis),
        "reference_words": len(reference_words),
        "hypothesis_words": len(hypothesis_words),
        "cer": char_counts["distance"] / max(1, len(reference)),
        "wer": (
            word_counts["distance"] / max(1, len(reference_words))
            if word_counts is not None else None),
        "character_edits": char_counts,
        "word_edits": word_counts,
        "wer_note": (
            None if calculate_wer
            else "Unavailable for Chinese without a word segmenter"),
    }


def _open_local_path(path):
    path = os.path.abspath(path)
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    if sys.platform == "win32":
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def _start_server_for_gui(port, app, server=None):
    """Accept/reaccept the camera without reloading local model weights."""
    if server is None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server.bind(("0.0.0.0", port))
            server.listen(1)
            server.settimeout(0.5)
        except Exception:
            server.close()
            raise
    app.register_sockets(server, None)
    app.publish_event("status", state="WAITING FOR PI", pi="WAITING",
                      message=f"Waiting for camera on TCP {port}")
    while not app.stop_requested.is_set():
        if terminal_action(app.key_queue) == "quit":
            app.stop_requested.set()
            break
        try:
            client, addr = server.accept()
            client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            client.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
            if AUDIO_ROUTER is not None:
                AUDIO_ROUTER.set_expected_peer(addr[0])
            app.register_sockets(server, client)
            app.publish_event("status", state="READY", pi="CONNECTED",
                              message=f"Camera connected: {addr[0]}")
            return server, client
        except socket.timeout:
            continue
    server.close()
    raise InterruptedError("Session closing")


def _ensure_box5_tts_import_path():
    """Locate the unchanged pipertts.py from the existing final workspace."""
    candidates = [
        PIPELINE_DIR,
        os.path.join(PROJECT_DIR, "1Pipeline", "final"),
        os.path.join(
            PROJECT_DIR, "1Pipeline", "final", "working", "Ahead",
            "Further Box AutoCapture", "TEST"),
    ]
    for candidate in candidates:
        if os.path.isfile(os.path.join(candidate, "pipertts.py")):
            if candidate not in sys.path:
                sys.path.insert(0, candidate)
            return candidate
    raise RuntimeError("Existing pipertts.py was not found in the project")


class Box5App:
    """Existing GUI layout retained for Box6; class name kept for compatibility."""

    SOURCE_OPTIONS = ("French", "Chinese", "Spanish", "English")
    OUTPUT_OPTIONS = ("English", "Urdu")
    SPEED_OPTIONS = ("1.0x", "1.5x", "2.0x")

    def __init__(self, root, test_mode=False):
        self.root = root
        self.test_mode = test_mode
        self.stop_requested = threading.Event()
        self.ui_events = queue.Queue()
        self._last_audio_event = None
        self._frame_lock = threading.Lock()
        self._latest_frame = None
        self._latest_frame_name = "NO VIDEO"
        self._last_frame_publish = 0.0
        self.key_queue = None
        self.pipeline_event_queue = None
        self.pipeline_thread = None
        self.server_socket = None
        self.client_socket = None
        self.tts_modules = []
        self.debug_session_dir = None
        self.last_ocr_text = ""
        self.last_translated_text = ""
        self.last_paragraphs = []
        self.last_run_data = {}
        self.last_metrics = {}
        self._photo = None
        self._audio_active = False
        self._audio_paused = False
        self._wave_tick = 0
        self._hidden_tabs = []

        self.source_var = tk.StringVar(value="French")
        self.book_mode_var = tk.BooleanVar(value=False)
        self.output_var = tk.StringVar(value="English")
        self.speed_var = tk.StringVar(value="1.0x")
        self.demo_var = tk.BooleanVar(value=False)
        self.debug_record_var = tk.BooleanVar(value=DEBUG_SESSION_RECORDING)
        self.audio_pi_var = tk.BooleanVar(value=False)
        self.audio_router = None
        self.state_var = tk.StringVar(value="NOT INITIALIZED")
        self.pi_var = tk.StringVar(value="DISCONNECTED")
        self.models_var = tk.StringVar(value="NOT LOADED")
        self.active_output_var = tk.StringVar(value="ENGLISH")
        self.message_var = tk.StringVar(value="Configure settings, then initialize")
        self.view_var = tk.StringVar(value="NO VIDEO")
        self.region_var = tk.StringVar(value="No active paragraph")

        self._configure_window()
        self._build_interface()
        self._bind_keys()
        self.speed_var.trace_add("write", self._speed_changed)
        self.root.protocol("WM_DELETE_WINDOW", self.request_close)
        self.root.after(50, self._poll)
        self.root.after(120, self._animate_audio)

    def _configure_window(self):
        self.root.title("FYDP Smart Glasses")
        self.root.geometry("1220x790")
        self.root.minsize(980, 620)
        self.root.configure(bg="#d4d0c8")
        style = ttk.Style(self.root)
        available = style.theme_names()
        if "winnative" in available:
            style.theme_use("winnative")
        elif "classic" in available:
            style.theme_use("classic")
        style.configure("TLabel", font=("Tahoma", 9))
        style.configure("TButton", font=("Tahoma", 9), padding=(5, 2))
        style.configure("TCheckbutton", font=("Tahoma", 9))
        style.configure("TCombobox", font=("Tahoma", 9))
        style.configure("TNotebook.Tab", font=("Tahoma", 9), padding=(8, 3))
        style.configure("Treeview", font=("Consolas", 9), rowheight=20)
        style.configure("Treeview.Heading", font=("Tahoma", 9, "bold"))

    def _build_interface(self):
        self.status_strip = tk.Frame(
            self.root, bg="#d4d0c8", bd=1, relief=tk.RAISED)
        self.status_strip.pack(fill=tk.X, padx=4, pady=(4, 2))
        self._status_cell(self.status_strip, "SYSTEM", self.state_var, 16)
        self._status_cell(self.status_strip, "PI LINK", self.pi_var, 14)
        self._status_cell(self.status_strip, "MODELS", self.models_var, 14)
        self._status_cell(
            self.status_strip, "OUTPUT", self.active_output_var, 12)
        tk.Label(
            self.status_strip, textvariable=self.message_var,
            anchor="w", bg="#d4d0c8", fg="#202020",
            font=("Tahoma", 9), padx=8).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Checkbutton(
            self.status_strip, text="Demo Mode", variable=self.demo_var,
            command=self._toggle_demo_mode).pack(side=tk.RIGHT, padx=8)

        self.main_pane = ttk.Panedwindow(self.root, orient=tk.HORIZONTAL)
        self.main_pane.pack(fill=tk.BOTH, expand=True, padx=4, pady=2)

        self.control_panel = tk.Frame(
            self.main_pane, bg="#d4d0c8", bd=1, relief=tk.GROOVE,
            width=235)
        self.main_pane.add(self.control_panel, weight=0)
        self._build_controls(self.control_panel)

        self.notebook = ttk.Notebook(self.main_pane)
        self.main_pane.add(self.notebook, weight=1)
        self.live_tab = ttk.Frame(self.notebook)
        self.text_tab = ttk.Frame(self.notebook)
        self.debug_tab = ttk.Frame(self.notebook)
        self.results_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.live_tab, text="Live")
        self.notebook.add(self.text_tab, text="Text Output")
        self.notebook.add(self.debug_tab, text="Debug")
        self.notebook.add(self.results_tab, text="Results")
        self._build_live_tab()
        self._build_text_tab()
        self._build_debug_tab()
        self._build_results_tab()

        command_bar = tk.Frame(
            self.root, bg="#d4d0c8", bd=1, relief=tk.RAISED)
        command_bar.pack(fill=tk.X, padx=4, pady=(2, 4))
        self.init_button = ttk.Button(
            command_bar, text="Initialize / Connect", command=self.start_system)
        self.init_button.pack(side=tk.LEFT, padx=3, pady=3)
        self.capture_button = ttk.Button(
            command_bar, text="Start Capture (S)",
            command=self.request_capture, state=tk.DISABLED)
        self.capture_button.pack(side=tk.LEFT, padx=3)
        self.pause_button = ttk.Button(
            command_bar, text="Pause / Resume (A)",
            command=self.request_pause, state=tk.DISABLED)
        self.pause_button.pack(side=tk.LEFT, padx=3)
        self.stop_button = ttk.Button(
            command_bar, text="Stop Audio",
            command=self.request_stop_audio, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, padx=3)
        ttk.Button(
            command_bar, text="Show OCR", command=self.show_text_tab).pack(
                side=tk.LEFT, padx=(14, 3))
        ttk.Button(
            command_bar, text="Open Debug", command=self.show_debug_tab).pack(
                side=tk.LEFT, padx=3)
        ttk.Button(
            command_bar, text="Exit", command=self.request_close).pack(
                side=tk.RIGHT, padx=3)

        tk.Label(
            self.root, textvariable=self.region_var, anchor="w",
            bg="#d4d0c8", fg="#202020", bd=1, relief=tk.SUNKEN,
            font=("Tahoma", 9), padx=5).pack(fill=tk.X, padx=4, pady=(0, 4))

    def _status_cell(self, parent, name, variable, width):
        cell = tk.Frame(parent, bg="#d4d0c8", bd=1, relief=tk.SUNKEN)
        cell.pack(side=tk.LEFT, padx=2, pady=2)
        tk.Label(
            cell, text=f"{name}:", bg="#d4d0c8", fg="#303030",
            font=("Tahoma", 8), padx=3).pack(side=tk.LEFT)
        tk.Label(
            cell, textvariable=variable, bg="#d4d0c8", fg="#000080",
            font=("Tahoma", 8, "bold"), width=width,
            anchor="w").pack(side=tk.LEFT)

    def _build_controls(self, parent):
        tk.Label(
            parent, text="STARTUP SETTINGS", anchor="w",
            bg="#808080", fg="white", font=("Tahoma", 9, "bold"),
            padx=5, pady=3).pack(fill=tk.X, padx=3, pady=(3, 8))
        body = tk.Frame(parent, bg="#d4d0c8")
        body.pack(fill=tk.BOTH, expand=True, padx=8)

        self.source_combo = self._labeled_combo(
            body, "Printed document language", self.source_var,
            self.SOURCE_OPTIONS)
        self.output_combo = self._labeled_combo(
            body, "Translation / audio output", self.output_var,
            self.OUTPUT_OPTIONS)
        self.speed_combo = self._labeled_combo(
            body, "Audio playback speed", self.speed_var,
            self.SPEED_OPTIONS)
        self.book_check = ttk.Checkbutton(
            body, text="Book mode (curved pages)",
            variable=self.book_mode_var)
        self.book_check.pack(anchor="w", pady=(8, 3))
        self.debug_check = ttk.Checkbutton(
            body, text="Record debug session",
            variable=self.debug_record_var)
        self.debug_check.pack(anchor="w", pady=3)
        self.audio_pi_check = ttk.Checkbutton(
            body, text="Audio(Pi)", variable=self.audio_pi_var,
            command=self._audio_route_changed)
        self.audio_pi_check.pack(anchor="w", pady=3)

        ttk.Separator(body, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=12)
        help_text = (
            "Controls remain active:\n"
            "S  Start/stop capture or audio\n"
            "A  Pause/resume audio\n"
            "R repeat | N next | P previous\n"
            "Q  Exit\n\n"
            "Raspberry Pi button commands use the same queues.")
        tk.Label(
            body, text=help_text, justify=tk.LEFT, anchor="nw",
            bg="#d4d0c8", fg="#303030", font=("Tahoma", 8)).pack(
                fill=tk.X)

    def _labeled_combo(self, parent, label, variable, values):
        ttk.Label(parent, text=label).pack(anchor="w", pady=(6, 2))
        combo = ttk.Combobox(
            parent, textvariable=variable, values=values,
            state="readonly", width=24)
        combo.pack(fill=tk.X)
        return combo

    def _build_live_tab(self):
        stage_bar = tk.Frame(
            self.live_tab, bg="#d4d0c8", bd=1, relief=tk.GROOVE)
        stage_bar.pack(fill=tk.X, padx=4, pady=4)
        self.stage_labels = {}
        for key, title in (
                ("camera", "CAMERA"), ("capture", "PAGE CHECK"),
                ("ocr", "OCR"), ("translation", "TRANSLATION"),
                ("audio", "AUDIO")):
            label = tk.Label(
                stage_bar, text=title, width=16, bg="#d4d0c8",
                fg="#202020", bd=1, relief=tk.SUNKEN,
                font=("Tahoma", 8, "bold"), pady=4)
            label.pack(side=tk.LEFT, padx=2, pady=2, expand=True, fill=tk.X)
            self.stage_labels[key] = label

        self.video_label = tk.Label(
            self.live_tab, text="Waiting for video", bg="#101010",
            fg="#c0c0c0", bd=2, relief=tk.SUNKEN,
            font=("Consolas", 11))
        self.video_label.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))

        audio_row = tk.Frame(
            self.live_tab, bg="#d4d0c8", bd=1, relief=tk.GROOVE)
        audio_row.pack(fill=tk.X, padx=4, pady=(0, 4))
        tk.Label(
            audio_row, text="AUDIO ACTIVITY", bg="#d4d0c8",
            font=("Tahoma", 8, "bold"), width=17).pack(side=tk.LEFT)
        self.audio_canvas = tk.Canvas(
            audio_row, height=38, bg="#202020", highlightthickness=1,
            highlightbackground="#808080")
        self.audio_canvas.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4, pady=3)
        self.audio_state_label = tk.Label(
            audio_row, text="IDLE", width=12, bg="#d4d0c8",
            fg="#202020", font=("Tahoma", 8, "bold"))
        self.audio_state_label.pack(side=tk.RIGHT, padx=5)

    def _build_text_tab(self):
        pane = ttk.Panedwindow(self.text_tab, orient=tk.HORIZONTAL)
        pane.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        left = ttk.Labelframe(pane, text="OCR output")
        right = ttk.Labelframe(pane, text="Translated / spoken output")
        pane.add(left, weight=1)
        pane.add(right, weight=1)
        self.ocr_text = ScrolledText(
            left, wrap=tk.WORD, font=("Consolas", 10), undo=False)
        self.ocr_text.pack(fill=tk.BOTH, expand=True, padx=3, pady=3)
        self.translation_text = ScrolledText(
            right, wrap=tk.WORD, font=("Nirmala UI", 11), undo=False)
        self.translation_text.pack(fill=tk.BOTH, expand=True, padx=3, pady=3)

    def _build_debug_tab(self):
        toolbar = tk.Frame(self.debug_tab, bg="#d4d0c8")
        toolbar.pack(fill=tk.X, padx=4, pady=4)
        ttk.Button(
            toolbar, text="Open Session Folder",
            command=self.open_debug_folder).pack(side=tk.LEFT)
        ttk.Button(
            toolbar, text="Clear View",
            command=self.clear_debug_view).pack(side=tk.LEFT, padx=4)
        self.debug_note = tk.StringVar(value="No capture analysis available")
        ttk.Label(toolbar, textvariable=self.debug_note).pack(
            side=tk.LEFT, padx=12)

        pane = ttk.Panedwindow(self.debug_tab, orient=tk.VERTICAL)
        pane.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))
        metrics_frame = ttk.Labelframe(pane, text="Latest quality measurements")
        regions_frame = ttk.Labelframe(pane, text="OCR regions and confidence")
        pane.add(metrics_frame, weight=1)
        pane.add(regions_frame, weight=1)

        self.debug_tree = ttk.Treeview(
            metrics_frame, columns=("value",), show="tree headings")
        self.debug_tree.heading("#0", text="Measurement")
        self.debug_tree.heading("value", text="Value")
        self.debug_tree.column("#0", width=270, stretch=True)
        self.debug_tree.column("value", width=210, stretch=True)
        self.debug_tree.pack(fill=tk.BOTH, expand=True)

        self.region_tree = ttk.Treeview(
            regions_frame,
            columns=("type", "confidence", "bbox", "text"),
            show="headings")
        for column, title, width in (
                ("type", "Type", 100), ("confidence", "Confidence", 90),
                ("bbox", "Bounding box", 150), ("text", "Text", 520)):
            self.region_tree.heading(column, text=title)
            self.region_tree.column(column, width=width, stretch=True)
        self.region_tree.pack(fill=tk.BOTH, expand=True)

    def _build_results_tab(self):
        top = ttk.Panedwindow(self.results_tab, orient=tk.HORIZONTAL)
        top.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        source_frame = ttk.Labelframe(top, text="Source ground truth")
        target_frame = ttk.Labelframe(
            top, text="Optional translation ground truth")
        top.add(source_frame, weight=1)
        top.add(target_frame, weight=1)
        self.source_truth = ScrolledText(
            source_frame, height=9, wrap=tk.WORD, font=("Consolas", 10))
        self.source_truth.pack(fill=tk.BOTH, expand=True, padx=3, pady=3)
        self.target_truth = ScrolledText(
            target_frame, height=9, wrap=tk.WORD, font=("Nirmala UI", 10))
        self.target_truth.pack(fill=tk.BOTH, expand=True, padx=3, pady=3)

        result_bar = tk.Frame(self.results_tab, bg="#d4d0c8")
        result_bar.pack(fill=tk.X, padx=4)
        ttk.Button(
            result_bar, text="Load Source Ground Truth",
            command=self.load_ground_truth).pack(side=tk.LEFT)
        ttk.Button(
            result_bar, text="Calculate CER / WER",
            command=self.calculate_results).pack(side=tk.LEFT, padx=4)
        ttk.Button(
            result_bar, text="Export Results JSON",
            command=self.export_results).pack(side=tk.LEFT)

        self.results_tree = ttk.Treeview(
            self.results_tab, columns=("value", "notes"), show="headings")
        self.results_tree.heading("value", text="Value")
        self.results_tree.heading("notes", text="Measurement")
        self.results_tree.column("value", width=160, stretch=False)
        self.results_tree.column("notes", width=760, stretch=True)
        self.results_tree.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

    def _bind_keys(self):
        self.root.bind_all("<KeyPress>", self._key_pressed, add="+")

    def _key_pressed(self, event):
        widget_class = event.widget.winfo_class()
        if widget_class in (
                "Text", "Entry", "TEntry", "TCombobox", "Spinbox"):
            return
        key = (event.char or "").lower()
        if key == "s":
            self.request_capture()
            return "break"
        if key == "a":
            self.request_pause()
            return "break"
        if key in ("n", "p", "r") and self.key_queue is not None:
            self.key_queue.put(key)
            return "break"
        if key == "q":
            self.request_close()
            return "break"

    def settings(self):
        return {
            "language": self.source_var.get().strip().lower(),
            "book_mode": bool(self.book_mode_var.get()),
            "output_language": self.output_var.get().strip().lower(),
            "speed": float(self.speed_var.get().rstrip("x")),
            "debug_recording": bool(self.debug_record_var.get()),
            "audio_pi": bool(self.audio_pi_var.get()),
        }

    def publish_event(self, event_type, **payload):
        if event_type == "audio":
            state = (payload.get("active"), payload.get("paused"))
            if state == self._last_audio_event:
                return
            self._last_audio_event = state
        self.ui_events.put((event_type, payload))

    def publish_frame(self, window_name, frame):
        if frame is None:
            return
        now = time.monotonic()
        if now - self._last_frame_publish < 1.0 / 20.0:
            return
        self._last_frame_publish = now
        with self._frame_lock:
            self._latest_frame = frame.copy()
            self._latest_frame_name = window_name

    def register_controls(self, key_queue, event_queue):
        self.key_queue = key_queue
        self.pipeline_event_queue = event_queue
        self.publish_event("controls_ready")

    def register_sockets(self, server_socket, client_socket):
        self.server_socket = server_socket
        self.client_socket = client_socket

    def register_tts_modules(self, *modules):
        # Called by the pipeline worker. Store plain Python objects only; all
        # Tk variable reads remain on the main thread in _speed_changed().
        unique = []
        for module in modules:
            if module is not None and all(module is not item for item in unique):
                unique.append(module)
        self.tts_modules = unique
        self.publish_event("tts_modules_ready")

    def start_system(self):
        if self.pipeline_thread and self.pipeline_thread.is_alive():
            return
        self._lock_startup_controls()
        self.state_var.set("INITIALIZING")
        self.models_var.set("LOADING")
        self.active_output_var.set(self.output_var.get().strip().upper())
        self.message_var.set("Loading local models in background")
        if self.test_mode:
            self.key_queue = queue.Queue()
            self.pipeline_event_queue = queue.Queue()
            self.state_var.set("TEST READY")
            self.models_var.set("TEST MODE")
            self.capture_button.configure(state=tk.NORMAL)
            self.pause_button.configure(state=tk.NORMAL)
            self.stop_button.configure(state=tk.NORMAL)
            return
        current_settings = self.settings()
        self.pipeline_thread = threading.Thread(
            target=run_pipeline_gui,
            args=(self, current_settings),
            daemon=True, name="box5-pipeline")
        self.pipeline_thread.start()

    def _lock_startup_controls(self):
        self.init_button.configure(state=tk.DISABLED)
        self.source_combo.configure(state=tk.DISABLED)
        self.output_combo.configure(state=tk.DISABLED)
        self.book_check.configure(state=tk.DISABLED)
        self.debug_check.configure(state=tk.DISABLED)

    def request_capture(self):
        if self.key_queue is None:
            self.message_var.set("Initialize the system before capture")
            return
        self.key_queue.put("s")
        self.message_var.set("Capture command queued")

    def _audio_route_changed(self):
        enabled = bool(self.audio_pi_var.get())
        if self.audio_router is not None:
            self.audio_router.set_pi_enabled(enabled)
            self.message_var.set(self.audio_router.status)
        else:
            self.message_var.set("Pi audio selected" if enabled else "PC audio selected")

    def request_pause(self):
        if self.key_queue is None:
            self.message_var.set("Audio controls are not ready")
            return
        self.key_queue.put("a")

    def request_stop_audio(self):
        if self.pipeline_event_queue is not None:
            self.pipeline_event_queue.put({"cmd": "gui_stop_audio"})
        for module in self.tts_modules:
            if module.is_speaking():
                module.stop()

    def _speed_changed(self, *_args):
        try:
            speed = float(self.speed_var.get().rstrip("x"))
        except ValueError:
            return
        for module in self.tts_modules:
            try:
                module.set_speed(speed)
            except Exception:
                pass

    def request_close(self):
        if self.stop_requested.is_set():
            return
        self.stop_requested.set()
        if self.key_queue is not None:
            self.key_queue.put("q")
        for module in self.tts_modules:
            try:
                module.stop()
            except Exception:
                pass
        if self.audio_router is not None:
            self.audio_router.stop()
        for sock in (self.client_socket, self.server_socket):
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                    sock.close()
                except Exception:
                    pass
        self.root.after(100, self.root.destroy)

    def _poll(self):
        if not self.root.winfo_exists():
            return
        self._update_video()
        try:
            while True:
                event_type, payload = self.ui_events.get_nowait()
                self._handle_event(event_type, payload)
        except queue.Empty:
            pass
        self.root.after(50, self._poll)

    def _handle_event(self, event_type, payload):
        if event_type == "status":
            if "state" in payload:
                self.state_var.set(payload["state"])
            if "pi" in payload:
                self.pi_var.set(payload["pi"])
            if "models" in payload:
                self.models_var.set(payload["models"])
            if "output" in payload:
                self.active_output_var.set(payload["output"])
            if "message" in payload:
                self.message_var.set(payload["message"])
        elif event_type == "controls_ready":
            self.capture_button.configure(state=tk.NORMAL)
            self.pause_button.configure(state=tk.NORMAL)
            self.stop_button.configure(state=tk.NORMAL)
        elif event_type == "tts_modules_ready":
            self._speed_changed()
        elif event_type == "stage":
            self._set_stage(payload.get("stage"))
            if payload.get("status"):
                self.message_var.set(payload["status"])
        elif event_type == "quality_metrics":
            self._update_debug_metrics(payload)
        elif event_type == "ocr_result":
            self.last_ocr_text = payload.get("text", "")
            self.last_paragraphs = payload.get("paragraphs", [])
            self._replace_text(self.ocr_text, self.last_ocr_text)
            self._update_regions(self.last_paragraphs)
        elif event_type == "translation_result":
            self.last_translated_text = payload.get("text", "")
            self._replace_text(
                self.translation_text, self.last_translated_text,
                rtl=payload.get("output_language") == "urdu")
        elif event_type == "run_complete":
            self.last_run_data = payload
            self.last_paragraphs = payload.get("paragraphs", [])
            self._update_regions(self.last_paragraphs)
            self.message_var.set(
                f"Run {payload.get('run_count')} complete | "
                f"confidence {payload.get('average_confidence', 0):.3f}")
        elif event_type == "active_region":
            self.region_var.set(
                "No active paragraph" if payload.get("index") is None else
                f"Reading {payload.get('label', '')}: {payload.get('source_text', '')[:180]}")
        elif event_type == "audio_route":
            self.message_var.set(payload.get("message", "Audio route updated"))
        elif event_type == "audio":
            self._audio_active = bool(payload.get("active"))
            self._audio_paused = bool(payload.get("paused"))
        elif event_type == "debug_session":
            self.debug_session_dir = payload.get("path")
            self.debug_note.set(self.debug_session_dir or "Debug recording disabled")
        elif event_type == "warning":
            self.message_var.set(payload.get("message", "Warning"))
            messagebox.showwarning("Box6", payload.get("message", "Warning"))
        elif event_type == "error":
            self.state_var.set("ERROR")
            self.message_var.set(payload.get("message", "Pipeline error"))
            messagebox.showerror("Box6", payload.get("message", "Pipeline error"))
        elif event_type == "session_ended":
            self.server_socket = None
            self.client_socket = None
            self.key_queue = None
            self.pipeline_event_queue = None
            self.tts_modules = []
            self.capture_button.configure(state=tk.DISABLED)
            self.pause_button.configure(state=tk.DISABLED)
            self.stop_button.configure(state=tk.DISABLED)
            self._unlock_startup_controls()
            if payload.get("error"):
                self.state_var.set("ERROR")
                self.message_var.set(payload.get("message", "Pipeline error"))
            else:
                self.state_var.set("STOPPED")
                self.pi_var.set("DISCONNECTED")
                self.message_var.set(
                    f"Session ended | total runs: {payload.get('run_count', 0)}")

    def _update_video(self):
        with self._frame_lock:
            if self._latest_frame is None:
                return
            frame = self._latest_frame
            name = self._latest_frame_name
            self._latest_frame = None
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = Image.fromarray(rgb)
        width = max(640, self.video_label.winfo_width() - 12)
        height = max(360, self.video_label.winfo_height() - 12)
        image.thumbnail((width, height), Image.Resampling.LANCZOS)
        self._photo = ImageTk.PhotoImage(image=image)
        self.video_label.configure(image=self._photo, text="")
        self.view_var.set(name)

    def _set_stage(self, stage):
        for key, label in self.stage_labels.items():
            if key == stage:
                label.configure(bg="#000080", fg="white", relief=tk.RAISED)
            else:
                label.configure(bg="#d4d0c8", fg="#202020", relief=tk.SUNKEN)

    def _unlock_startup_controls(self):
        self.init_button.configure(state=tk.NORMAL)
        self.source_combo.configure(state="readonly")
        self.output_combo.configure(state="readonly")
        self.book_check.configure(state=tk.NORMAL)
        self.debug_check.configure(state=tk.NORMAL)

    def _animate_audio(self):
        if not self.root.winfo_exists():
            return
        self.audio_canvas.delete("all")
        width = max(20, self.audio_canvas.winfo_width())
        height = max(20, self.audio_canvas.winfo_height())
        bars = 32
        bar_width = max(2, width / bars - 2)
        for index in range(bars):
            if self._audio_active and not self._audio_paused:
                level = 0.20 + 0.70 * abs(
                    np.sin((index * 0.72) + (self._wave_tick * 0.55)))
                color = "#00c000"
            elif self._audio_paused:
                level = 0.18
                color = "#c08000"
            else:
                level = 0.08
                color = "#606060"
            x1 = 2 + index * (width / bars)
            x2 = x1 + bar_width
            y1 = height - 2 - level * (height - 6)
            self.audio_canvas.create_rectangle(
                x1, y1, x2, height - 2, fill=color, outline="")
        if self._audio_paused:
            state = "PAUSED"
        elif self._audio_active:
            state = "PLAYING"
        else:
            state = "IDLE"
        self.audio_state_label.configure(text=state)
        self._wave_tick += 1
        self.root.after(120, self._animate_audio)

    def _replace_text(self, widget, text, rtl=False):
        widget.configure(state=tk.NORMAL)
        widget.delete("1.0", tk.END)
        widget.insert("1.0", text or "")
        widget.tag_delete("box5_rtl")
        if rtl and text:
            # Keep logical Unicode unchanged for NLLB/TTS/logging. This tag is
            # display-only and gives Urdu a readable right-aligned text block.
            widget.tag_configure(
                "box5_rtl", justify=tk.RIGHT,
                lmargin1=12, lmargin2=12, rmargin=12)
            widget.tag_add("box5_rtl", "1.0", "end-1c")
        widget.configure(state=tk.DISABLED)

    def _update_debug_metrics(self, payload):
        for item in self.debug_tree.get_children():
            self.debug_tree.delete(item)
        assessment = payload.get("assessment", {})
        rows = {
            "stable_count": (
                f"{payload.get('stable_count', 0)}/"
                f"{payload.get('required', 0)}"),
            "capture_gate_passed": payload.get("passed", False),
        }
        for key, value in assessment.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                rows[key] = value
            elif isinstance(value, dict):
                for nested_key, nested_value in value.items():
                    if isinstance(nested_value, (str, int, float, bool)):
                        rows[f"{key}.{nested_key}"] = nested_value
        for key in sorted(rows):
            value = rows[key]
            if isinstance(value, float):
                value = f"{value:.4f}"
            self.debug_tree.insert("", tk.END, text=key, values=(value,))
        self.debug_note.set(
            assessment.get("guidance_text", "Capture analysis active"))

    def _update_regions(self, paragraphs):
        for item in self.region_tree.get_children():
            self.region_tree.delete(item)
        for paragraph in paragraphs:
            self.region_tree.insert(
                "", tk.END,
                values=(
                    paragraph.get("region_type", "paragraph"),
                    f"{float(paragraph.get('confidence', 0)):.3f}",
                    paragraph.get("bbox", ""),
                    paragraph.get("source_text", "")))

    def _toggle_demo_mode(self):
        if self.demo_var.get():
            try:
                self.main_pane.forget(self.control_panel)
            except tk.TclError:
                pass
            self._hidden_tabs = []
            for tab in (self.text_tab, self.debug_tab, self.results_tab):
                try:
                    self.notebook.hide(tab)
                    self._hidden_tabs.append(tab)
                except tk.TclError:
                    pass
            self.notebook.select(self.live_tab)
        else:
            panes = self.main_pane.panes()
            if str(self.control_panel) not in panes:
                self.main_pane.insert(0, self.control_panel, weight=0)
            for tab, title in (
                    (self.text_tab, "Text Output"),
                    (self.debug_tab, "Debug"),
                    (self.results_tab, "Results")):
                try:
                    self.notebook.add(tab, text=title)
                except tk.TclError:
                    pass
            self._hidden_tabs = []

    def show_text_tab(self):
        if self.demo_var.get():
            self.demo_var.set(False)
            self._toggle_demo_mode()
        self.notebook.select(self.text_tab)

    def show_debug_tab(self):
        if self.demo_var.get():
            self.demo_var.set(False)
            self._toggle_demo_mode()
        self.notebook.select(self.debug_tab)

    def clear_debug_view(self):
        for tree in (self.debug_tree, self.region_tree):
            for item in tree.get_children():
                tree.delete(item)
        self.debug_note.set("Debug view cleared")

    def open_debug_folder(self):
        path = self.debug_session_dir or DEBUG_SESSION_ROOT
        try:
            _open_local_path(path)
        except Exception as exc:
            messagebox.showerror("Open Debug Folder", str(exc))

    def load_ground_truth(self):
        path = filedialog.askopenfilename(
            title="Load source ground truth",
            filetypes=(("Text files", "*.txt"), ("All files", "*.*")))
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as handle:
                text = handle.read()
            self.source_truth.delete("1.0", tk.END)
            self.source_truth.insert("1.0", text)
        except Exception as exc:
            messagebox.showerror("Ground Truth", str(exc))

    def calculate_results(self):
        source_reference = self.source_truth.get("1.0", tk.END)
        target_reference = self.target_truth.get("1.0", tk.END)
        source_language = self.last_run_data.get(
            "source_language", self.source_var.get().strip().lower())
        ocr_metrics = calculate_text_metrics(
            source_reference, self.last_ocr_text,
            calculate_wer=source_language != "chinese")
        translation_metrics = calculate_text_metrics(
            target_reference, self.last_translated_text)
        self.last_metrics = {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "ocr": ocr_metrics,
            "translation": translation_metrics,
            "ocr_average_confidence": self.last_run_data.get(
                "average_confidence"),
            "timings": self.last_run_data.get("timings", {}),
        }
        for item in self.results_tree.get_children():
            self.results_tree.delete(item)
        confidence = self.last_metrics["ocr_average_confidence"]
        self.results_tree.insert(
            "", tk.END,
            values=(
                "N/A" if confidence is None else f"{confidence:.3f}",
                "Mean PaddleOCR confidence (not ground-truth accuracy)"))
        self._insert_metric_rows("OCR", ocr_metrics)
        self._insert_metric_rows("Translation", translation_metrics)
        for name, value in self.last_metrics["timings"].items():
            self.results_tree.insert(
                "", tk.END,
                values=(f"{float(value):.3f} s", f"Timing: {name}"))

    def _insert_metric_rows(self, prefix, metrics):
        if not metrics.get("available"):
            self.results_tree.insert(
                "", tk.END,
                values=("N/A", f"{prefix}: {metrics.get('reason')}"))
            return
        self.results_tree.insert(
            "", tk.END,
            values=(f"{metrics['cer']*100:.2f}%", f"{prefix} CER"))
        self.results_tree.insert(
            "", tk.END,
            values=(
                "N/A" if metrics["wer"] is None
                else f"{metrics['wer']*100:.2f}%",
                f"{prefix} WER" if metrics["wer"] is not None
                else f"{prefix} WER: {metrics.get('wer_note')}"))
        for unit in ("character", "word"):
            edits = metrics[f"{unit}_edits"]
            if edits is None:
                continue
            self.results_tree.insert(
                "", tk.END,
                values=(
                    f"S={edits['substitutions']} D={edits['deletions']} "
                    f"I={edits['insertions']}",
                    f"{prefix} {unit} edit counts"))

    def export_results(self):
        if not self.last_metrics:
            self.calculate_results()
        path = filedialog.asksaveasfilename(
            title="Export evaluation results",
            defaultextension=".json",
            filetypes=(("JSON files", "*.json"),))
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(self.last_metrics, handle, ensure_ascii=False, indent=2)
        except Exception as exc:
            messagebox.showerror("Export Results", str(exc))


def run_pipeline_gui(app, settings):
    """One model lifecycle; recover per-document and per-camera failures."""
    global SPELL_CORRECTOR, _GUI_CONTROLLER, AUDIO_ROUTER, _ACTIVE_DEBUG_RECORDER, _LAST_DOCUMENT
    _GUI_CONTROLLER = app
    _LAST_DOCUMENT = None
    debug_recorder = None
    server_sock = client_sock = None
    receiver = None
    run_count = 0
    pipeline_error = None
    english_tts = document_tts = None
    keyboard_stop = threading.Event()
    key_queue = ControlQueue()
    event_queue = ControlQueue()
    app.key_queue, app.pipeline_event_queue = key_queue, event_queue
    try:
        app.publish_event("status", state="INITIALIZING", models="LOADING",
                          message="Checking local OCR runtime")
        _preflight_paddle_native_dependencies()
        if settings.get("debug_recording", True):
            try:
                debug_recorder = DebugSessionRecorder()
                debug_recorder.start_console_capture()
                _ACTIVE_DEBUG_RECORDER = debug_recorder
                app.publish_event("debug_session", path=debug_recorder.session_dir)
            except Exception as exc:
                print(f"[DEBUG] Recording unavailable: {exc}")
        AUDIO_ROUTER = AudioRouter(event_callback=_audio_event)
        app.audio_router = AUDIO_ROUTER
        AUDIO_ROUTER.set_pi_enabled(settings.get("audio_pi", False))
        AUDIO_ROUTER.start()
        if getattr(app, "cli_mode", False):
            start_keyboard_listener(key_queue, keyboard_stop)
        app.publish_event("status", state="INITIALIZING", models="LOADING",
                          message="Loading local Piper voice")
        _ensure_box5_tts_import_path()
        english_tts = load_tts()
        english_tts.set_speed(settings["speed"])
        document_tts = english_tts
        effective_output_language = settings["output_language"]
        if effective_output_language == "urdu":
            try:
                document_tts = load_urdu_tts(english_tts)
                document_tts.set_speed(settings["speed"])
            except Exception as exc:
                effective_output_language = "english"
                app.publish_event("warning", message=f"Urdu voice unavailable; using English. {exc}")
        app.register_tts_modules(english_tts, document_tts)
        _announce_status(english_tts, "Welcome to F Y D P Glasses")
        language, book_mode = settings["language"], settings["book_mode"]
        app.publish_event("status", message="Loading OCR")
        ocr_engine = load_ocr_engine(language)
        capture_detector = CaptureDetector(ocr_engine)
        print(f"[CAPTURE] Detection-only available: {capture_detector.detection_only_available}")
        unwarper = load_unwarper() if book_mode else None
        app.publish_event("status", message="Loading local translation model")
        translator, tokenizer = load_nllb_translator_for_output(language, effective_output_language)
        try:
            SPELL_CORRECTOR = load_spell_corrector()
        except Exception as exc:
            SPELL_CORRECTOR = None
            print(f"[SPELL] Original text retained: {exc}")
        app.register_controls(key_queue, event_queue)
        app.publish_event("status", models="READY",
                          output=effective_output_language.upper())
        _announce_status(english_tts, "All models loaded")
        while not app.stop_requested.is_set() and terminal_action(key_queue) != "quit":
            event_queue = ControlQueue()
            app.register_controls(key_queue, event_queue)
            server_sock, client_sock = _start_server_for_gui(PORT, app, server_sock)
            frame_holder = FrameHolder()
            receiver = threading.Thread(
                target=receiver_loop, args=(client_sock, frame_holder, event_queue, debug_recorder),
                daemon=True, name="box6-camera")
            receiver.start()
            connected_at = time.monotonic()
            _announce_status(english_tts, "Smart glasses connected. Press S to start.")
            app.publish_event("stage", stage="camera", status="Ready; press S to capture")
            while not app.stop_requested.is_set() and not terminal_action(key_queue, event_queue):
                frame, sequence, timestamp = frame_holder.get_snapshot()
                if time.monotonic() - (timestamp if frame is not None else connected_at) > STREAM_TIMEOUT_SECONDS:
                    event_queue.put({"event": "disconnect", "error": "No fresh camera frames"})
                    break
                if frame is not None:
                    _display_frame("Smart Glasses Live Stream", frame, cli_size=(640, 360))
                actions = pending_actions(key_queue, event_queue)
                if "quit" in actions or "disconnect" in actions:
                    break
                try:
                    if "capture" in actions:  # S/capture button in idle mode starts capture.
                        chosen = pre_capture_quality_loop(
                            frame_holder, capture_detector, english_tts,
                            key_queue, event_queue, debug_recorder)
                        if chosen is not None and not terminal_action(key_queue, event_queue):
                            run_count += 1
                            process_and_speak(
                                chosen, run_count, language, book_mode, ocr_engine,
                                unwarper, translator, tokenizer, english_tts,
                                key_queue, event_queue, output_language=effective_output_language,
                                document_tts_module=document_tts)
                    else:
                        voice = getattr(app, "voice_service", None)
                        voice_command = voice.take_command() if voice is not None and voice.enabled else None
                        if voice_command is not None:
                            voice.finish_command(restore=False)
                            from box7_reading import idle_command
                            idle_command(sys.modules[__name__], voice_command, key_queue, event_queue)
                        navigation = next((a for a in reversed(actions)
                                           if a in ("next", "previous", "repeat")), None)
                        if navigation:
                            _replay_last_document(navigation, key_queue, event_queue)
                except Exception as exc:
                    if AUDIO_ROUTER is not None:
                        AUDIO_ROUTER.stop()
                    print(f"[RUN ERROR] {type(exc).__name__}: {exc}")
                    _safe_debug_call(debug_recorder, "record_event", "run_failed",
                                     run=run_count, error=str(exc))
                    app.publish_event("status", state="READY",
                                      message=f"Run failed: {exc}. Press S to retry.")
                    _gui_emit("audio", active=False, paused=False)
                    _gui_emit("stage", stage="camera", status="Ready to retry or repeat")
                    if not (AUDIO_ROUTER.pi_enabled and not AUDIO_ROUTER.connected):
                        try:
                            _announce_status(english_tts, "Could not read. Please try again.")
                        except Exception:
                            pass
                time.sleep(0.05)
            if AUDIO_ROUTER is not None:
                AUDIO_ROUTER.stop()
            _close_socket(client_sock)
            client_sock = None
            if receiver is not None:
                receiver.join(timeout=1.0)
            frame_holder.clear()
            if (app.stop_requested.is_set()
                    or terminal_action(key_queue, event_queue) == "quit"):
                break
            _safe_debug_call(debug_recorder, "record_event", "waiting_for_reconnect")
            app.publish_event("status", state="WAITING FOR PI", pi="DISCONNECTED",
                              message="Camera disconnected; waiting to reconnect")
    except InterruptedError:
        pass
    except Exception as exc:
        if not app.stop_requested.is_set():
            pipeline_error = str(exc)
            print(f"[BOX6 ERROR] {exc}")
            app.publish_event("error", message=str(exc))
    finally:
        keyboard_stop.set()
        voice = getattr(app, "voice_service", None)
        if voice is not None:
            voice.cancel(restore=False)
        _close_socket(client_sock)
        _close_socket(server_sock)
        if receiver is not None:
            receiver.join(timeout=1.0)
        if AUDIO_ROUTER is not None:
            AUDIO_ROUTER.close()
        app.audio_router = None
        AUDIO_ROUTER = None
        _LAST_DOCUMENT = None
        cv2.destroyAllWindows()
        if debug_recorder is not None:
            try:
                debug_recorder.close()
            except Exception as exc:
                print(f"[DEBUG] Finalization failed: {exc}")
        _ACTIVE_DEBUG_RECORDER = None
        app.publish_event("session_ended", run_count=run_count,
                          error=bool(pipeline_error), message=pipeline_error)
        _GUI_CONTROLLER = None


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════
def run_pipeline(debug_recorder=None):
    """CLI uses the same tested session/control/reconnect path as the GUI."""
    print(BANNER)
    settings = {"language": ask_language(), "book_mode": ask_book_mode(),
                "output_language": "english", "speed": 1.0,
                "debug_recording": DEBUG_SESSION_RECORDING, "audio_pi": "--audio-pi" in sys.argv}
    class ConsoleApp:
        cli_mode = True
        def __init__(self):
            self.stop_requested = threading.Event()
            self.key_queue = self.pipeline_event_queue = None
            self.audio_router = None
        def publish_event(self, event_type, **payload):
            if event_type in ("status", "stage", "error", "warning"):
                print(f"[{event_type.upper()}] {payload.get('message', payload.get('status', ''))}")
        def publish_frame(self, name, frame):
            cv2.imshow(name, frame)
            key = cv2.waitKey(1) & 0xFF
            if key in map(ord, "asqnpr") and self.key_queue is not None:
                self.key_queue.put(chr(key))
        def register_controls(self, keys, events):
            self.key_queue, self.pipeline_event_queue = keys, events
        def register_sockets(self, server, client):
            self.server_socket, self.client_socket = server, client
        def register_tts_modules(self, *modules):
            self.tts_modules = modules
    run_pipeline_gui(ConsoleApp(), settings)


def main_cli():
    try:
        run_pipeline()
    except KeyboardInterrupt:
        print("[EXIT] Interrupted")


def main():
    """Start Box6 in GUI mode, or use the shared console engine with --cli."""
    if "--cli" in sys.argv:
        main_cli()
        return

    root = tk.Tk()
    from box7_gui import make_app_class
    make_app_class(sys.modules[__name__])(root)
    root.mainloop()


if __name__ == "__main__":
    main()
