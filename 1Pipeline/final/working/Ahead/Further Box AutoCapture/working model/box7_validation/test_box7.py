"""Behavioral Box7 tests: no live microphone, camera, models or speaker."""
import ast
import contextlib
import hashlib
import io
import json
from pathlib import Path
import queue
import socket
import struct
import sys
import threading
import time
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from box7_commands import CaptureHold, parse_command, resolve_selection
from box7_voice import VoiceService
from box7_voice_io import receive_packet, send_packet
from box7_runtime import PlaybackControls, VoiceAction
from box6_runtime import ControlQueue


def wait_for(predicate, timeout=3):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for test condition")


class FakeTTS:
    def __init__(self, active=False, paused=False):
        self.active, self.paused = active, paused
        self.sent = []
        self.last_error = None
    def speak(self, text): self.sent.append(text); self.active = False
    def is_speaking(self): return self.active
    def is_paused(self): return self.paused
    def pause(self): self.paused = True
    def resume(self): self.paused = False
    def stop(self): self.active = False; self.paused = False
    def wait_until_done(self): return True
    def set_speed(self, value): pass


class FakeRecognizer:
    def __init__(self, text="repeat paragraph two"):
        self.text, self.calls, self.closed = text, 0, 0
        self.entered = threading.Event()
        self.release = threading.Event()
        self.release.set()
    def check(self): pass
    def transcribe(self, pcm, cancelled=None):
        self.calls += 1
        self.entered.set()
        self.release.wait(3)
        return {"text": self.text, "seconds": 0.01}
    def close(self): self.closed += 1


def regions():
    return [dict(region_id=i + 1, region_type=kind, paragraph_number=number,
                 heading_number=1, spoken_text=text, source_text=text,
                 bbox=(0, i * 20, 100, i * 20 + 18))
            for i, (kind, number, text) in enumerate([
                ("page_title", None, "Device manual"), ("paragraph", 1, "First body text"),
                ("section_heading", None, "Operation"), ("paragraph", 2, "Second body text"),
                ("paragraph", 3, "Third body text")])]


class CommandTests(unittest.TestCase):
    def test_requested_phrases(self):
        for text, action, number in [
            ("repeat the paragraph", "repeat_current", None),
            ("Repeat paragraph 1.", "read_number", 1),
            ("please repeat paragraph two", "read_number", 2),
            ("read the third paragraph", "read_number", 3),
            ("read from paragraph 2", "read_from", 2),
            ("skip paragraph one", "skip_number", 1),
            ("what's the heading?", "heading", None),
            ("repeat the headings", "headings", None),
            ("read all headings", "headings", None),
            ("how many paragraphs are there?", "count", None),
            ("where am I?", "position", None),
            ("continue reading", "resume", None),
            ("what voltage does the camera require?", "question", None)]:
            with self.subTest(text=text):
                parsed = parse_command(text)
                self.assertEqual((parsed.action, parsed.number), (action, number))

    def test_numbers_use_body_labels_not_region_positions(self):
        self.assertEqual(resolve_selection(parse_command("repeat paragraph 2"), regions()), ([3], "preview"))
        self.assertEqual(resolve_selection(parse_command("read from paragraph 2"), regions()), ([3, 4], "continue"))

    def test_skip_is_explicit_navigation(self):
        self.assertEqual(resolve_selection(parse_command("skip paragraph 1"), regions()), ([2, 3, 4], "continue"))

    def test_invalid_or_ambiguous_commands_do_not_execute(self):
        for text in ("", "repeat paragraph zero", "repeat paragraph two or three", "banana next paragraph", "repeat paragraph minus two"):
            self.assertEqual(parse_command(text).action, "unknown")
        self.assertEqual(parse_command("what does next paragraph mean?").action, "question")
        with self.assertRaisesRegex(ValueError, "not present"):
            resolve_selection(parse_command("read paragraph 12"), regions())

    def test_heading_selection_and_missing_heading(self):
        self.assertEqual(resolve_selection(parse_command("what is the heading"), regions(), 4), ([2], "preview"))
        self.assertEqual(resolve_selection(parse_command("repeat headings"), regions()), ([0, 2], "preview"))
        with self.assertRaisesRegex(ValueError, "No heading"):
            resolve_selection(parse_command("repeat headings"), [regions()[1]])

    def test_capture_disabled_preserves_press_action(self):
        events = []; hold = CaptureHold(events.append)
        hold.press(0, False); hold.tick(2); hold.release(3)
        self.assertEqual(events, ["capture"])

    def test_short_capture_only_on_release(self):
        events = []; hold = CaptureHold(events.append)
        hold.press(0, True); hold.tick(0.3)
        self.assertEqual(events, [])
        hold.release(0.4)
        self.assertEqual(events, ["capture"])

    def test_long_hold_never_captures_and_repeat_press_ignored(self):
        events = []; hold = CaptureHold(events.append)
        hold.press(0, True); hold.press(0.1, True); hold.tick(0.71); hold.tick(1.1); hold.release(2); hold.release(3)
        self.assertEqual(events, ["ptt_start", "ptt_end"])

    def test_hold_threshold_even_without_timer_tick(self):
        events = []; hold = CaptureHold(events.append)
        hold.press(0, True); hold.release(0.8)
        self.assertEqual(events, ["ptt_start", "ptt_end"])

    def test_lost_focus_cancel_does_not_capture(self):
        events = []; hold = CaptureHold(events.append)
        hold.press(0, True); hold.tick(1); hold.cancel(); hold.release(2)
        self.assertEqual(events, ["ptt_start", "ptt_cancel"])


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.phase = "camera"
        self.tts = FakeTTS(True)
        self.events = []
        self.asr = FakeRecognizer()
        self.service = VoiceService(ROOT, lambda: (self.phase, [self.tts]),
            lambda kind, **data: self.events.append((kind, data)), port=0, recognizer=self.asr)
        self.service.configure(True)
    def tearDown(self):
        self.service.close(); self.asr.release.set(); PlaybackControls.voice = None

    def test_enable_is_idle_without_any_recognition(self):
        self.assertEqual(self.asr.calls, 0)
        self.assertFalse(self.service.busy)

    def test_pause_record_transcribe_numbered_command(self):
        token = self.service.begin(); self.assertTrue(self.tts.paused)
        self.service.submit(b"\x01\x02" * 5000, token)
        wait_for(lambda: not self.service._commands.empty())
        self.assertEqual(self.service.take_command().number, 2)

    def test_unknown_restores_playback_and_is_not_queued(self):
        self.asr.text = "gibberish"
        token = self.service.begin(); self.service.submit(b"\x01\x02" * 5000, token)
        wait_for(lambda: not self.service.busy)
        self.assertFalse(self.tts.paused)
        self.assertIsNone(self.service.take_command())

    def test_cancel_preserves_preexisting_pause(self):
        self.tts.paused = True
        self.service.begin(); self.service.cancel()
        self.assertTrue(self.tts.paused)

    def test_disable_during_transcription_discards_result(self):
        self.asr.release.clear()
        token = self.service.begin(); self.service.submit(b"\x01\x02" * 5000, token)
        self.asr.entered.wait(2)
        self.service.configure(False); self.asr.release.set()
        time.sleep(0.05)
        self.assertIsNone(self.service.take_command())
        self.assertFalse(self.service.enabled)
        self.assertFalse(self.tts.paused)

    def test_reject_processing_requests_and_short_recordings(self):
        for phase in ("initializing", "capture", "ocr", "translation", "stopped"):
            self.phase = phase
            self.assertIsNone(self.service.begin())
        self.phase = "camera"
        token = self.service.begin()
        self.assertFalse(self.service.submit(b"\0" * 100, token))
        self.assertFalse(self.service.busy)
        self.assertEqual(self.asr.calls, 0)

    def test_timeout_restores_audio(self):
        token = self.service.begin(); self.service._timeout(token)
        self.assertFalse(self.service.busy)
        self.assertFalse(self.tts.paused)

    def test_stop_wins_over_voice_command(self):
        self.service._commands.put(parse_command("repeat paragraph two"))
        PlaybackControls.voice = self.service
        keys = ControlQueue(); keys.put("s")
        self.assertEqual(PlaybackControls().poll(self.tts, keys, ControlQueue()), "stop")
        self.assertIsNone(self.service.take_command())

    def test_explicit_pause_is_not_a_toggle(self):
        self.tts.paused = True
        PlaybackControls.voice = self.service
        self.service._commands.put(parse_command("pause"))
        self.assertIsNone(PlaybackControls().poll(self.tts, ControlQueue(), ControlQueue()))
        self.assertTrue(self.tts.paused)

    def test_keyboard_resume_cancels_recording(self):
        self.service.begin()
        PlaybackControls.voice = self.service
        keys = ControlQueue(); keys.put("a")
        self.assertIsNone(PlaybackControls().poll(self.tts, keys, ControlQueue()))
        self.assertFalse(self.service.busy)
        self.assertFalse(self.tts.paused)
        self.assertTrue(self.service._asr_cancel.is_set())

    def test_numbered_action_interrupts_playback(self):
        PlaybackControls.voice = self.service
        self.service._commands.put(parse_command("repeat paragraph two"))
        action = PlaybackControls().poll(self.tts, ControlQueue(), ControlQueue())
        self.assertIsInstance(action, VoiceAction)
        self.assertEqual(action.command.number, 2)
        self.assertFalse(self.tts.active)

    def test_real_socket_begin_pcm_and_stale_generation(self):
        with socket.create_connection(("127.0.0.1", self.service.port), timeout=2) as sock:
            lock = threading.Lock()
            self.assertEqual(receive_packet(sock)[0]["op"], "config")
            send_packet(sock, lock, {"op": "begin", "request": 1})
            token = receive_packet(sock)[0]["token"]
            send_packet(sock, lock, {"op": "audio", "token": token}, b"\x01\x02" * 5000)
            wait_for(lambda: not self.service._commands.empty())
            self.assertEqual(self.service.take_command().action, "read_number")
            self.service.cancel()
            self.assertFalse(self.service.submit(b"\x01\x02" * 5000, token))

    def test_pi_client_handshake_with_mock_microphone(self):
        import box7_pi_voice
        class Mic:
            def __init__(self, device): pass
            def start(self): pass
            def stop(self): return b"\x01\x02" * 5000
        client = box7_pi_voice.PiVoiceClient("127.0.0.1", self.service.port)
        with patch.object(box7_pi_voice, "Recorder", Mic):
            try:
                client.start(); wait_for(lambda: client.enabled)
                client.begin(); wait_for(lambda: client._recording); client.end()
                wait_for(lambda: not self.service._commands.empty())
                self.assertEqual(self.service.take_command().number, 2)
            finally:
                client.close()


class VoiceEdgeTests(unittest.TestCase):
    def select(self, sources):
        from box7_voice_io import linux_input_source
        result = types.SimpleNamespace(returncode=0, stdout=json.dumps(sources))
        with patch("box7_voice_io.shutil.which", return_value="pactl"), \
             patch("box7_voice_io.subprocess.run", return_value=result):
            return linux_input_source("default")

    def test_bluetooth_selection_excludes_monitor(self):
        self.assertEqual(self.select([{"name": "bluez_output.qcy.monitor"},
                                     {"name": "bluez_input.qcy"}]), "bluez_input.qcy")

    def test_multiple_bluetooth_selects_identified_qcy(self):
        self.assertEqual(self.select([{"name": "bluez_input.other"},
            {"name": "bluez_input.earbuds", "description": "QCY ArcBuds"}]), "bluez_input.earbuds")

    def test_missing_or_ambiguous_bluetooth_is_explicit_error(self):
        for sources in ([{"name": "alsa_input.pc"}],
                        [{"name": "bluez_input.one"}, {"name": "bluez_input.two"}]):
            with self.subTest(sources=sources), self.assertRaises(RuntimeError):
                self.select(sources)

    def test_explicit_microphone_never_runs_autoselection(self):
        from box7_voice_io import linux_input_source
        with patch("box7_voice_io.subprocess.run") as run:
            self.assertEqual(linux_input_source("alsa_input.usb"), "alsa_input.usb")
            run.assert_not_called()

    def test_cancelled_recognition_never_starts_worker(self):
        from box7_voice import WhisperProcess
        cancelled = threading.Event(); cancelled.set()
        with patch("box7_voice.subprocess.Popen") as spawn:
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                WhisperProcess(ROOT).transcribe(b"\0" * 4000, cancelled=cancelled)
            spawn.assert_not_called()


class ProtocolTests(unittest.TestCase):
    def test_fragmented_pcm_packet(self):
        pcm = b"\x01\x02" * 100
        head = json.dumps({"op": "audio", "pcm_bytes": len(pcm)}).encode()
        wire = struct.pack("!I", len(head)) + head + pcm
        a, b = socket.socketpair()
        def send():
            with a:
                for i in range(0, len(wire), 7): a.sendall(wire[i:i+7])
        thread = threading.Thread(target=send); thread.start()
        with b:
            self.assertEqual(receive_packet(b)[1], pcm)
        thread.join()

    def test_oversized_or_odd_pcm_rejected(self):
        for count in (960002, -1, 5, "100"):
            a, b = socket.socketpair()
            with a, b:
                data = json.dumps({"pcm_bytes": count}).encode()
                a.sendall(struct.pack("!I", len(data)) + data)
                with self.assertRaises(ValueError): receive_packet(b)


class HostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import pipeline_cli_box6, pipeline_cli_box7
        cls.old, cls.new = pipeline_cli_box6, pipeline_cli_box7

    def setUp(self):
        self.new._GUI_CONTROLLER = None; self.new._LAST_DOCUMENT = None
        self.new._ACTIVE_DEBUG_RECORDER = None; PlaybackControls.voice = None

    def test_protected_files_hashes(self):
        expected = {"pipeline_cli_box6.py": "99d33d8afb07497872b40c09a822254c65298aec2deaa0fbc1ae646babdc1747",
            "piweb_cli3.py": "93ddd141345e3e350e6f2ee30c06839141cd75388b3c524083ad24633c58214c",
            "box6_audio.py": "f4ff6fba8be69d1c22d5099ea5a6c28a6e805da1e040ac34500bc4d09ad7589e"}
        for name, digest in expected.items():
            self.assertEqual(hashlib.sha256((ROOT/name).read_bytes()).hexdigest(), digest)

    def test_core_algorithms_identical_ast(self):
        def funcs(name):
            return {n.name: ast.dump(n) for n in ast.parse((ROOT/name).read_text(encoding="utf-8-sig")).body if isinstance(n, ast.FunctionDef)}
        old, new = funcs("pipeline_cli_box6.py"), funcs("pipeline_cli_box7.py")
        for name in ("preprocess_image", "run_ocr", "_extract_ocr_lines", "merge_ocr_fragments_into_lines",
            "group_ocr_lines_into_paragraphs", "analyze_capture_frame", "pre_capture_quality_loop",
            "load_ocr_engine", "load_nllb_translator_for_output", "run_translation_nllb_paragraphs_for_output",
            "run_spell_correction", "draw_paragraph_overlay"):
            with self.subTest(name=name): self.assertEqual(old[name], new[name])
        oldpi, newpi = funcs("piweb_cli3.py"), funcs("piweb_cli4.py")
        for name in ("send_json_safe", "send_frame_safe", "_pause_button_callback"):
            self.assertEqual(oldpi[name], newpi[name])

    def test_voice_disabled_speaks_same_segments(self):
        import numpy as np
        records = []
        for module in (self.old, self.new):
            tts = FakeTTS()
            with patch.object(module, "show_paragraph_preview"), patch.object(module, "_gui_emit"):
                module.run_tts_paragraphs(tts, regions(), np.zeros((200, 200, 3)), ControlQueue(), ControlQueue())
            records.append(tts.sent)
        self.assertEqual(records[0], records[1])

    def test_heading_preview_restores_bookmark_and_numbered_repeat(self):
        import numpy as np
        from box7_reading import idle_command
        tts = FakeTTS()
        doc = self.new._LAST_DOCUMENT = dict(tts=tts, paragraphs=regions(), image=np.zeros((200, 200, 3)),
            output_language="english", index=3, voice_bookmark=3)
        with patch.object(self.new, "show_paragraph_preview"), patch.object(self.new, "_gui_emit"):
            idle_command(self.new, parse_command("repeat headings"), ControlQueue(), ControlQueue())
            self.assertEqual(len(tts.sent), 2)
            self.assertIn("Device manual", tts.sent[0]); self.assertIn("Operation", tts.sent[1])
            self.assertEqual(doc["index"], 3)
            tts.sent.clear()
            idle_command(self.new, parse_command("repeat paragraph one"), ControlQueue(), ControlQueue())
            self.assertEqual(len(tts.sent), 1); self.assertIn("First body text", tts.sent[0])
            self.assertEqual(doc["index"], 3)
            tts.sent.clear()
            idle_command(self.new, parse_command("continue reading"), ControlQueue(), ControlQueue())
            self.assertEqual(len(tts.sent), 2); self.assertIn("Second body text", tts.sent[0])

    def test_active_preview_automatically_returns_and_stop_still_wins(self):
        import numpy as np
        from box7_reading import run
        for commands, expected in [
            (["read paragraph 2"], [1, 3, 1, 2, 3, 4]),
            (["read headings"], [1, 0, 2, 1, 2, 3, 4]),
            (["read paragraph 2", "read headings"], [1, 3, 0, 2, 1, 2, 3, 4]),
            (["read paragraph 2", "STOP"], [1, 3]),
            (["read paragraph 2", "read from paragraph 3"], [1, 3, 4]),
        ]:
            with self.subTest(commands=commands):
                doc = self.new._LAST_DOCUMENT = dict(index=1, voice_bookmark=1, paragraphs=regions())
                seen, pending = [], list(commands)
                def speak(*args):
                    seen.append(doc["index"])
                    if pending:
                        command = pending.pop(0)
                        return "stop" if command == "STOP" else VoiceAction(parse_command(command))
                    return "finished"
                with patch.object(self.new, "show_paragraph_preview"), patch.object(self.new, "_gui_emit"), \
                     patch.object(self.new, "_speak_segment_with_stop", side_effect=speak):
                    run(self.new, FakeTTS(), regions(), np.zeros((200,200,3)), start_index=1)
                self.assertEqual(seen, expected)
                self.assertIs(self.new._LAST_DOCUMENT, doc)

    def test_question_is_transcribed_not_falsely_answered(self):
        from box7_reading import idle_command
        events = []
        with patch.object(self.new, "_gui_emit", side_effect=lambda k, **p: events.append((k, p))):
            idle_command(self.new, parse_command("What voltage is required?"), ControlQueue(), ControlQueue())
        self.assertTrue(any(k == "voice_question" and "Stage 2" in p["status"] for k,p in events))

    def test_gui_disabled_default_g_and_text_field_guard(self):
        import tkinter as tk
        from box7_gui import make_app_class
        root = tk.Tk(); root.withdraw()
        app = make_app_class(self.new)(root, test_mode=True)
        try:
            app.start_system(); root.update()
            self.assertFalse(app.voice_var.get())
            self.assertIsNone(app.voice_service.recognizer.process)
            with patch.object(app.voice_service, "start_input") as start, patch.object(app.voice_service, "end_input") as end:
                app._key_pressed(types.SimpleNamespace(char="g", widget=types.SimpleNamespace(winfo_class=lambda: "TEntry")))
                start.assert_not_called()
                event = types.SimpleNamespace(char="g", widget=types.SimpleNamespace(winfo_class=lambda: "Frame"))
                app._key_pressed(event); app._key_pressed(event)
                self.assertEqual(start.call_count, 1)
                app._finish_g(); self.assertEqual(end.call_count, 1)
            app.capture_button.invoke()
            self.assertEqual(app.key_queue.get_nowait(), "s")
            app.pause_button.invoke()
            self.assertEqual(app.key_queue.get_nowait(), "a")
        finally:
            app.voice_service.close(); PlaybackControls.voice = None
            for timer in root.tk.call("after", "info"):
                root.after_cancel(timer)
            root.destroy()


if __name__ == "__main__":
    unittest.main()
