"""Focused Box10 auto-capture guidance regressions; no hardware is required."""
import hashlib
import queue
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pipeline_cli_box10 as host


def guidance(last_state=None, last_any=0.0):
    instance = object.__new__(host.AudioGuidance)
    instance._queue = queue.Queue(maxsize=1)
    instance._stop_event = threading.Event()
    instance._last_by_state = {}
    instance._last_any = last_any
    instance._last_state = last_state
    instance._last_vertical_state = (
        last_state if last_state in ("look_up", "look_down") else None)
    instance._vertical_reversal_active = False
    instance._state_cooldown = 4.5
    instance._global_cooldown = 0.8
    instance._event_callback = None
    return instance


class AutocaptureTests(unittest.TestCase):
    def test_box9_is_preserved(self):
        digest = hashlib.sha256((ROOT / "pipeline_cli_box9.py").read_bytes()).hexdigest()
        self.assertEqual(
            digest,
            "433c05e106e5f22015afdb177835b557fe59808c2c780b47d3bcb254db27f163",
        )

    def test_look_up_then_look_down_becomes_move_back(self):
        instance = guidance("look_up", 10.0)
        with patch.object(host.time, "monotonic", return_value=11.0):
            instance.notify("look_down")
        self.assertEqual(instance._queue.get_nowait(), "move_back")
        self.assertEqual(instance._last_state, "move_back")

    def test_look_down_then_look_up_becomes_move_back(self):
        instance = guidance("look_down", 10.0)
        with patch.object(host.time, "monotonic", return_value=11.0):
            instance.notify("look_up")
        self.assertEqual(instance._queue.get_nowait(), "move_back")

    def test_move_back_advice_stays_latched_while_vertically_clipped(self):
        instance = guidance("look_up", 10.0)
        with patch.object(host.time, "monotonic", return_value=11.0):
            instance.notify("look_down")
        self.assertEqual(instance._queue.get_nowait(), "move_back")
        with patch.object(host.time, "monotonic", return_value=16.0):
            instance.notify("look_down")
        self.assertEqual(instance._queue.get_nowait(), "move_back")

    def test_repeated_direction_is_unchanged(self):
        instance = guidance("look_up", 1.0)
        with patch.object(host.time, "monotonic", return_value=10.0):
            instance.notify("look_up")
        self.assertEqual(instance._queue.get_nowait(), "look_up")

    def test_horizontal_opposite_rule_is_preserved(self):
        instance = guidance("look_left", 10.0)
        with patch.object(host.time, "monotonic", return_value=11.0):
            instance.notify("look_right")
        self.assertTrue(instance._queue.empty())

    def test_capture_again_uses_spoken_clip(self):
        with patch.object(host, "_GUIDANCE_CLIPS", {"capture_again": object()}), \
                patch.object(host, "_play_guidance_clip") as play:
            host.announce_capture_again(object())
        play.assert_called_once_with("capture_again")

    def test_capture_again_falls_back_to_old_failure_tone(self):
        with patch.object(host, "_play_guidance_clip", side_effect=KeyError), \
                patch.object(host, "beep_failure") as beep:
            host.announce_capture_again()
        beep.assert_called_once_with()

    def test_unreadable_saved_capture_speaks_retry(self):
        tts = object()
        with patch.object(host, "_processing_cancelled", return_value=False), \
                patch.object(host, "beep_capture"), \
                patch.object(host, "save_frame", return_value="missing.jpg"), \
                patch.object(host.cv2, "imread", return_value=None), \
                patch.object(host, "announce_capture_again") as announce:
            result = host.process_and_speak(
                object(), 1, "english", False, None, None, None, None, tts)
        self.assertFalse(result)
        announce.assert_called_once_with(tts)

    def test_soft_saved_capture_speaks_retry(self):
        tts = object()
        details = {
            "sharpness": host.POST_CAPTURE_LAP_MIN - 1,
            "sharpness_pts": 1,
            "brightness_pts": 2,
            "evenness_pts": 3,
        }
        with patch.object(host, "_processing_cancelled", return_value=False), \
                patch.object(host, "beep_capture"), \
                patch.object(host, "save_frame", return_value="soft.jpg"), \
                patch.object(host.cv2, "imread", return_value=object()), \
                patch.object(host, "score_frame_quality", return_value=(0, details)), \
                patch.object(host, "announce_capture_again") as announce:
            result = host.process_and_speak(
                object(), 1, "english", False, None, None, None, None, tts)
        self.assertFalse(result)
        announce.assert_called_once_with(tts)

    def test_book_mode_policy_block_falls_back_to_flat_ocr(self):
        error = ImportError(
            "DLL load failed while importing libpaddle: "
            "An Application Control policy has blocked this file.")
        with patch.object(host, "load_unwarper", side_effect=error):
            unwarper, enabled, warning = host.load_optional_unwarper(True)
        self.assertIsNone(unwarper)
        self.assertFalse(enabled)
        self.assertIn("Book Mode was disabled", warning)

    def test_unrelated_unwarper_failure_is_not_hidden(self):
        with patch.object(host, "load_unwarper", side_effect=RuntimeError("bad model")):
            with self.assertRaisesRegex(RuntimeError, "bad model"):
                host.load_optional_unwarper(True)

    def test_second_batched_capture_press_is_preserved_as_force(self):
        keys = host.ControlQueue()
        count = host._preserve_extra_capture_presses(
            ["capture", "capture"], keys)
        self.assertEqual(count, 1)
        self.assertEqual(host.capture_action(keys, host.ControlQueue()), "force")

    def test_single_capture_press_does_not_create_force(self):
        keys = host.ControlQueue()
        count = host._preserve_extra_capture_presses(["capture"], keys)
        self.assertEqual(count, 0)
        self.assertIsNone(host.capture_action(keys, host.ControlQueue()))

    def test_force_capture_marks_capture_metadata(self):
        holder = host.FrameHolder()
        image = host.np.ones((10, 10, 3), host.np.uint8)
        holder.update(image)
        keys = host.ControlQueue()
        keys.put("s")
        metadata = {}
        with patch.object(host, "AudioGuidance", return_value=MagicMock()), \
                patch.object(host, "beep_ready"):
            selected = host.pre_capture_quality_loop(
                holder, None, None, keys, host.ControlQueue(),
                capture_metadata=metadata)
        self.assertIs(selected, image)
        self.assertTrue(metadata["forced"])

    def test_forced_soft_frame_continues_to_ocr(self):
        tts = MagicMock()
        details = {
            "sharpness": host.POST_CAPTURE_LAP_MIN - 1,
            "sharpness_pts": 1,
            "brightness_pts": 2,
            "evenness_pts": 3,
        }
        with patch.object(host, "_processing_cancelled", return_value=False), \
                patch.object(host, "beep_capture"), \
                patch.object(host, "save_frame", return_value="forced-soft.jpg"), \
                patch.object(host.cv2, "imread", return_value=object()), \
                patch.object(host, "score_frame_quality", return_value=(0, details)), \
                patch.object(host, "_display_frame"), \
                patch.object(host, "run_ocr", return_value=("", [], None, 0)) as run_ocr, \
                patch.object(host, "tone_failure"), \
                patch.object(host, "announce_capture_again") as announce:
            result = host.process_and_speak(
                object(), 1, "english", False, None, None, None, None, tts,
                force_capture=True)
        self.assertFalse(result)
        run_ocr.assert_called_once()
        announce.assert_not_called()


if __name__ == "__main__":
    unittest.main()
