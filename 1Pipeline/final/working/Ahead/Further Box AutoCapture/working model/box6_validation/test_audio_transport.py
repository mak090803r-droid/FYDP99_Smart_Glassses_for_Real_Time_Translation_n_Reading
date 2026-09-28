"""Offline Box6 audio tests. No camera, Pi, model inference or speakers used."""
import json
from pathlib import Path
import socket
import struct
import sys
import threading
import time
import types
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import box6_audio as audio


def eventually(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("Condition did not become true before timeout")


class FakePlayer:
    def __init__(self, duration=0.18):
        self.duration = duration
        self.started = threading.Event()
        self.records = []
        self.state = None
        self.finished = threading.Event()
        self.progress = 0
        self.fail = False

    def available(self):
        return "fake"

    def play(self, pcm, sample_rate, state):
        self.state = state
        self.records.append((pcm.copy(), sample_rate))
        self.started.set()
        if self.fail:
            raise RuntimeError("simulated unplugged speaker")
        active = 0
        while active < self.duration and not state.cancel.is_set():
            time.sleep(0.005)
            if not state.paused.is_set():
                active += 0.005 * state.speed
                self.progress += 1
        self.finished.set()


class FakePiper:
    def __init__(self):
        self.calls = []
        self.block_old = threading.Event()
        self.old_started = threading.Event()

    def _synthesize(self, text):
        self.calls.append(text)
        if text == "old":
            self.old_started.set()
            self.block_old.wait(3)
        value = 111 if text == "old" else 222
        return [types.SimpleNamespace(
            sample_rate=22050, sample_channels=1,
            audio_int16_array=np.full(2205, value, dtype=np.int16))]


class AudioTests(unittest.TestCase):
    def setUp(self):
        self.local = FakePlayer(duration=0.04)
        self.events = []
        self.router = audio.AudioRouter(
            port=0, local_player=self.local,
            event_callback=lambda event, **payload: self.events.append((event, payload)))
        self.pi = None

    def tearDown(self):
        if self.pi:
            self.pi.close()
        self.router.close()

    def connect_pi(self, duration=0.18):
        self.remote = FakePlayer(duration=duration)
        self.router.start()
        eventually(lambda: self.router.server._listener is not None)
        self.pi = audio.PiAudioClient(
            "127.0.0.1", port=self.router.server.port,
            player=self.remote, retry_delay=0.05, status_callback=None)
        self.pi.start()
        eventually(lambda: self.router.connected)
        self.router.set_pi_enabled(True)

    def test_closing_releases_audio_port_for_next_session(self):
        self.router.start()
        eventually(lambda: self.router.server._listener is not None)
        port = self.router.server.port
        self.router.close()
        replacement = audio.RemoteAudioServer(port=port, host='127.0.0.1')
        try:
            replacement.start()
            eventually(lambda: replacement._listener is not None)
            self.assertFalse(self.router.server._accept_thread.is_alive())
        finally:
            replacement.close()

    def launch_audio(self, state=None, seconds=0.2):
        self.errors = []
        self.playback_done = threading.Event()
        pcm = np.arange(int(22050 * seconds), dtype=np.int16).reshape(-1, 1)

        def work():
            try:
                self.router.play_samples(pcm, 22050, state)
            except Exception as exc:
                self.errors.append(str(exc))
            finally:
                self.playback_done.set()
        thread = threading.Thread(target=work)
        thread.start()
        self.addCleanup(thread.join, 3)
        return pcm, thread

    def test_local_default_and_exact_pcm(self):
        pcm = np.arange(2000, dtype=np.int16)
        self.router.play_samples(pcm, 22050)
        self.assertEqual(len(self.local.records), 1)
        np.testing.assert_array_equal(self.local.records[0][0][:, 0], pcm)
        self.assertFalse(self.router.pi_enabled)

    def test_pi_pcm_and_completion_ack(self):
        self.connect_pi()
        pcm, thread = self.launch_audio()
        self.assertTrue(self.remote.started.wait(2))
        self.assertFalse(self.playback_done.is_set())
        thread.join(3)
        self.assertTrue(self.playback_done.is_set())
        self.assertTrue(self.remote.finished.is_set())
        self.assertEqual(self.errors, [])
        np.testing.assert_array_equal(self.remote.records[0][0], pcm)
        self.assertEqual(self.local.records, [])

    def test_pause_resume_speed_and_stop_while_paused(self):
        self.connect_pi(duration=1)
        state = audio.PlaybackState()
        _, thread = self.launch_audio(state)
        self.assertTrue(self.remote.started.wait(2))
        state.control("pause")
        eventually(lambda: self.remote.state.paused.is_set())
        time.sleep(0.02)
        count = self.remote.progress
        time.sleep(0.05)
        self.assertEqual(self.remote.progress, count)
        self.assertFalse(self.playback_done.is_set())
        state.speed = 1.5
        state.control("resume")
        eventually(lambda: not self.remote.state.paused.is_set())
        eventually(lambda: self.remote.state.speed == 1.5)
        eventually(lambda: self.remote.progress > count)
        state.control("pause")
        eventually(lambda: self.remote.state.paused.is_set())
        state.control("stop")
        thread.join(3)
        self.assertTrue(self.playback_done.is_set())
        self.assertTrue(self.remote.state.cancel.is_set())
        self.assertEqual(self.errors, [])

    def test_route_toggle_stops_old_sink_without_duplicate(self):
        self.connect_pi(duration=1)
        _, thread = self.launch_audio()
        self.assertTrue(self.remote.started.wait(2))
        started = time.monotonic()
        self.router.set_pi_enabled(False)
        self.assertLess(time.monotonic() - started, 0.1)
        thread.join(3)
        self.assertTrue(self.remote.state.cancel.is_set())
        self.assertEqual(self.local.records, [])
        self.router.play_samples(np.ones(1000, dtype=np.int16), 22050)
        self.assertEqual(len(self.local.records), 1)
        self.assertEqual(len(self.remote.records), 1)

    def test_disconnect_wakes_waiter_and_surfaces_failure(self):
        self.connect_pi(duration=2)
        _, thread = self.launch_audio()
        self.assertTrue(self.remote.started.wait(2))
        self.pi.close()
        thread.join(3)
        self.assertTrue(self.playback_done.is_set())
        self.assertTrue(self.errors)
        self.assertIn("disconnect", self.errors[0].lower())
        self.assertEqual(self.local.records, [])

    def test_remote_playback_error_reaches_piper_caller(self):
        self.connect_pi()
        self.remote.fail = True
        tts = self.router.wrap_tts(FakePiper())
        tts.speak("test")
        with self.assertRaisesRegex(RuntimeError, "unplugged"):
            tts.wait_until_done(timeout=3)
        with self.assertRaisesRegex(RuntimeError, "unplugged"):
            tts.check_errors()
        self.assertFalse(tts.is_speaking())

    def test_disconnected_pi_does_not_silently_play_on_pc(self):
        self.router.set_pi_enabled(True)
        tts = self.router.wrap_tts(FakePiper())
        tts.speak("test")
        with self.assertRaisesRegex(RuntimeError, "disconnected"):
            tts.wait_until_done(timeout=3)
        self.assertEqual(self.local.records, [])

    def test_stop_during_synthesis_cannot_resurrect_old_text(self):
        module = FakePiper()
        tts = self.router.wrap_tts(module)
        tts.speak("old")
        self.assertTrue(module.old_started.wait(2))
        tts.stop()
        self.assertFalse(tts.is_speaking())
        self.assertTrue(tts.wait_until_done(timeout=0.05))
        tts.speak("new")
        module.block_old.set()
        self.assertTrue(tts.wait_until_done(timeout=3))
        self.assertEqual(module.calls, ["old", "new"])
        self.assertEqual(len(self.local.records), 1)
        actual = self.local.records[0][0]
        self.assertEqual(set(np.unique(actual)), {0, 222})

    def test_prefetch_and_repeat_reuse_bounded_cache(self):
        module = FakePiper()
        tts = self.router.wrap_tts(module)
        self.assertTrue(tts.prefetch("next"))
        eventually(lambda: "next" in tts._cache)
        tts.speak("next")
        self.assertTrue(tts.wait_until_done(timeout=3))
        tts.speak("next")
        self.assertTrue(tts.wait_until_done(timeout=3))
        self.assertEqual(module.calls, ["next"])
        self.assertLessEqual(tts._cache_bytes, 16 * 1024 * 1024)

    def test_canonical_hardware_control_reaches_host(self):
        self.connect_pi()
        self.assertTrue(self.pi.send_control("next"))
        eventually(lambda: any(
            event == "pi_control" and payload["action"] == "next"
            for event, payload in self.events))
        with self.assertRaises(ValueError):
            self.pi.send_control("arbitrary-command")

    def test_invalid_packet_lengths_are_rejected(self):
        for prefix in (0, audio.MAX_HEADER_BYTES + 1):
            first, second = socket.socketpair()
            try:
                first.sendall(struct.pack("!I", prefix))
                with self.assertRaises(ValueError):
                    audio._recv_packet(second)
            finally:
                first.close()
                second.close()
        first, second = socket.socketpair()
        try:
            header = json.dumps({"pcm_bytes": -1}).encode()
            first.sendall(struct.pack("!I", len(header)) + header)
            with self.assertRaises(ValueError):
                audio._recv_packet(second)
        finally:
            first.close()
            second.close()

    def test_pcm_backend_pause_preserves_unsent_samples_exactly(self):
        pcm = np.arange(4000, dtype=np.int16).reshape(-1, 1)

        def run(pausing):
            output = []
            state = audio.PlaybackState()
            paused = threading.Event()

            class Stream:
                def __init__(self, **kwargs):
                    pass
                def start(self):
                    pass
                def write(self, data):
                    output.append(data)
                    if pausing and len(output) == 1:
                        state.paused.set()
                        paused.set()
                def stop(self):
                    pass
                def abort(self):
                    pass
                def close(self):
                    pass

            def resume():
                self.assertTrue(paused.wait(2))
                time.sleep(0.05)
                self.assertEqual(len(output), 1)
                state.paused.clear()

            thread = None
            if pausing:
                thread = threading.Thread(target=resume)
                thread.start()
            with mock.patch.dict(sys.modules, {
                    "sounddevice": types.SimpleNamespace(RawOutputStream=Stream)}):
                audio.PCMPlayer().play(pcm, 22050, state)
            if thread:
                thread.join(2)
            return b"".join(output)

        normal = run(False)
        paused = run(True)
        self.assertEqual(normal, paused)
        self.assertGreater(len(normal), len(pcm.tobytes()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
