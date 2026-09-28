"""Host integration regressions. No models, camera or audible output are loaded."""
import ast
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import pickle
import queue
import socket
import struct
import sys
import threading
import time
import types
import unittest
from unittest.mock import patch, MagicMock

os.environ['HF_HUB_OFFLINE'] = '1'
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
import cv2
import pipeline_cli_box6 as host
from box6_runtime import ControlQueue, pending_actions, terminal_action, capture_action, recv_camera_message


class SilentTTS:
    def __init__(self):
        self.active = False
        self.paused = False
        self.last_error = None
        self.sent = []
    def speak(self, text):
        self.sent.append(text)
        self.active = False
    def is_speaking(self): return self.active
    def stop(self): self.active = False
    def pause(self): self.paused = True
    def resume(self): self.paused = False
    def is_paused(self): return self.paused
    def wait_until_done(self): return True
    def set_speed(self, speed): pass


class HostTests(unittest.TestCase):
    def setUp(self):
        host._GUI_CONTROLLER = None
        host._ACTIVE_DEBUG_RECORDER = None
        host._LAST_DOCUMENT = None
        host._PLAYBACK_ACTIVE.clear()
        host._PADDLE_NATIVE_PREFLIGHT_DONE = False

    def test_paddlex_safety_flags_are_set_before_third_party_imports(self):
        source = (ROOT/'pipeline_cli_box6.py').read_text(encoding='utf-8-sig')
        eager = source.index('os.environ["PADDLE_PDX_EAGER_INIT"] = "False"')
        self.assertLess(eager, source.index('import torch'))
        self.assertEqual(host.os.environ['PADDLE_PDX_EAGER_INIT'], 'False')

    def test_blocked_native_dll_fails_before_paddlex_and_can_retry(self):
        with patch.object(host.importlib, 'import_module',
                          side_effect=ImportError('DLL load failed while importing indexing: An Application Control policy has blocked this file.')):
            with self.assertRaisesRegex(RuntimeError, 'Windows blocked a required local OCR DLL'):
                host._preflight_paddle_native_dependencies()
        self.assertFalse(host._PADDLE_NATIVE_PREFLIGHT_DONE)
        with patch.object(host.importlib, 'import_module', return_value=object()):
            host._preflight_paddle_native_dependencies()
        self.assertTrue(host._PADDLE_NATIVE_PREFLIGHT_DONE)

    def test_protected_baselines(self):
        for name, expected in {
            'pipeline_cli_box5.py': 'D66748988B9C917A158EA2F3A453B2C6D3BBCF122DC5ACC57D023125CAFF43EC',
            'piweb_cli2.py': '38B0D236E553DED37FD09A8AAE0DE092B47352BEE66F306086AB9E63027D0FEE',
        }.items():
            self.assertEqual(hashlib.sha256((ROOT/name).read_bytes()).hexdigest().upper(), expected)

    def test_paragraph_and_heading_functions_preserved(self):
        def functions(path):
            src = path.read_text(encoding='utf-8-sig')
            return {n.name: ast.dump(n, include_attributes=False) for n in ast.parse(src).body if isinstance(n, ast.FunctionDef)}
        old, new = functions(ROOT/'pipeline_cli_box5.py'), functions(ROOT/'pipeline_cli_box6.py')
        for name in ('_extract_ocr_lines', 'merge_ocr_fragments_into_lines', 'group_ocr_lines_into_paragraphs', 'draw_paragraph_overlay'):
            self.assertEqual(old[name], new[name])

    def test_fragmented_legacy_jpeg_and_json(self):
        frame = np.full((32, 48, 3), 180, np.uint8)
        ok, jpg = cv2.imencode('.jpg', frame)
        self.assertTrue(ok)
        for protocol in (4, 5):
            payload = pickle.dumps(jpg, protocol=protocol)
            control = json.dumps({'cmd': 'tts_control', 'action': 'pause'}).encode()
            wire = struct.pack('!BQ', 2, len(payload)) + payload + struct.pack('!BI', 1, len(control)) + control
            left, right = socket.socketpair()
            def send():
                with left:
                    for index in range(0, len(wire), 7): left.sendall(wire[index:index+7])
            thread = threading.Thread(target=send)
            thread.start()
            with right:
                kind, decoded = recv_camera_message(right)
                self.assertEqual(kind, 2)
                self.assertEqual(decoded.shape, frame.shape)
                self.assertEqual(recv_camera_message(right), (1, {'cmd': 'tts_control', 'action': 'pause'}))
            thread.join()

    def test_invalid_packets_rejected(self):
        packets = [struct.pack('!BQ', 2, 17*1024*1024),
                   struct.pack('!BI', 1, 2) + b'[]',
                   struct.pack('!BQ', 2, 4) + b'nope']
        for packet in packets:
            left, right = socket.socketpair()
            with left, right:
                left.sendall(packet)
                left.shutdown(socket.SHUT_WR)
                with self.assertRaises((ValueError, pickle.UnpicklingError, EOFError)):
                    recv_camera_message(right)

    def test_terminal_events_survive_queue_drain(self):
        for event in ('q', {'cmd':'quit'}, {'event':'disconnect'}):
            q = ControlQueue(); q.put(event)
            pending_actions(q)
            self.assertIsNotNone(terminal_action(q))

    def test_stop_audio_does_not_start_idle_capture(self):
        q = ControlQueue(); q.put({'cmd':'gui_stop_audio'})
        self.assertEqual(pending_actions(q), ['stop'])
        q.put('s'); self.assertEqual(pending_actions(q), ['capture'])

    def test_capture_quit_with_no_frame(self):
        keys = ControlQueue(); keys.put('q')
        with patch.object(host, 'AudioGuidance', return_value=MagicMock()):
            self.assertIsNone(host.pre_capture_quality_loop(host.FrameHolder(), None, None, keys, ControlQueue()))

    def test_stalled_frame_cannot_be_force_captured(self):
        holder = host.FrameHolder(); holder.update(np.ones((10,10,3), np.uint8))
        holder._timestamp = time.monotonic() - 20
        keys, events = ControlQueue(), ControlQueue(); keys.put('s')
        with patch.object(host, 'AudioGuidance', return_value=MagicMock()):
            self.assertIsNone(host.pre_capture_quality_loop(holder, None, None, keys, events))
        self.assertTrue(events.disconnected.is_set())

    def test_force_capture_uses_fresh_frame(self):
        holder = host.FrameHolder(); image = np.ones((10,10,3), np.uint8); holder.update(image)
        keys = ControlQueue(); keys.put('s')
        with patch.object(host, 'AudioGuidance', return_value=MagicMock()), patch.object(host, 'beep_ready'):
            self.assertIs(host.pre_capture_quality_loop(holder, None, None, keys, ControlQueue()), image)

    def test_receiver_disconnect_clears_image(self):
        left, right = socket.socketpair(); left.close()
        holder = host.FrameHolder(); holder.update(np.ones((10,10,3), np.uint8))
        events = ControlQueue()
        host.receiver_loop(right, holder, events); right.close()
        self.assertIsNone(holder.get())
        self.assertTrue(events.disconnected.is_set())

    def test_empty_unwarper_is_recoverable(self):
        with patch.object(host.cv2, 'imread', return_value=np.zeros((30,30,3),np.uint8)):
            with self.assertRaisesRegex(RuntimeError, 'UVDoc returned no'):
                host.run_ocr(None, 'fake.jpg', True, types.SimpleNamespace(predict=lambda *a, **kw: []))

    def test_ocr_generator_is_consumed_under_lock(self):
        def predict(image):
            self.assertTrue(host.OCR_LOCK.locked())
            yield {'rec_texts': ['Hello'], 'rec_scores':[0.99], 'rec_boxes':[[1,1,20,12]]}
        with patch.object(host.cv2, 'imread', return_value=np.zeros((30,30,3),np.uint8)):
            text, regions, _, _ = host.run_ocr(types.SimpleNamespace(predict=predict), 'fake.jpg', False, None)
        self.assertEqual(text, 'Hello'); self.assertEqual(len(regions), 1)

    def test_paragraph_navigation_and_quit(self):
        regions = [dict(number=i+1, region_id=i+10, paragraph_number=i+1,
                        source_text=f'Source {i}', spoken_text=f'Speech {i}', bbox=(0,0,10,10)) for i in range(3)]
        visited = []
        def overlay(image, regions, active_index=None):
            if active_index is not None: visited.append(active_index)
        with patch.object(host, 'show_paragraph_preview', side_effect=overlay), \
             patch.object(host, 'paragraph_page_location', return_value='at the top of the page'), \
             patch.object(host, '_speak_segment_with_stop', side_effect=['next','previous','repeat','finished','finished','quit']):
            host.run_tts_paragraphs(SilentTTS(), regions, np.zeros((10,10,3)), ControlQueue(), ControlQueue())
        self.assertEqual(visited, [0,1,0,0,1,2])

    def test_q_in_speech_remains_visible(self):
        tts = SilentTTS(); keys = ControlQueue(); keys.put('q')
        self.assertEqual(host._speak_segment_with_stop(tts, 'never play', keys, ControlQueue()), 'quit')
        self.assertEqual(terminal_action(keys), 'quit'); self.assertEqual(tts.sent, [])

    def test_tk_checkbox_and_keyboard_wiring(self):
        import tkinter as tk
        root = tk.Tk(); root.withdraw()
        try:
            app = host.Box5App(root, test_mode=True)
            self.assertEqual(app.audio_pi_check.cget('text'), 'Audio(Pi)')
            self.assertFalse(app.audio_pi_var.get())
            app.audio_router = MagicMock(status='Pi audio connected')
            app.audio_pi_var.set(True); app._audio_route_changed()
            app.audio_router.set_pi_enabled.assert_called_with(True)
            app.audio_pi_var.set(False); app._audio_route_changed()
            app.audio_router.set_pi_enabled.assert_called_with(False)
            app.key_queue = ControlQueue()
            for key in ('n','p','r'):
                app._key_pressed(types.SimpleNamespace(widget=root, char=key))
            self.assertEqual(pending_actions(app.key_queue), ['next','previous','repeat'])
        finally:
            for timer in root.tk.call('after', 'info'): root.after_cancel(timer)
            root.destroy()

    def _session_fixture(self):
        app = types.SimpleNamespace(stop_requested=threading.Event(), cli_mode=False,
            publish_event=MagicMock(), register_sockets=MagicMock(),
            register_tts_modules=MagicMock())
        def controls(keys, events):
            app.key_queue, app.pipeline_event_queue = keys, events
        app.register_controls = controls
        settings = dict(language='english', output_language='english', speed=1.0,
                        book_mode=False, debug_recording=False, audio_pi=False)
        return app, settings

    def _session_patches(self, stack):
        mocks = {}
        replacements = {
            'AudioRouter': MagicMock(return_value=MagicMock(pi_enabled=False, connected=False)),
            '_ensure_box5_tts_import_path': MagicMock(), 'load_tts': MagicMock(return_value=SilentTTS()),
            '_announce_status': MagicMock(), 'load_ocr_engine': MagicMock(return_value=object()),
            'CaptureDetector': MagicMock(return_value=MagicMock(detection_only_available=True)),
            'load_unwarper': MagicMock(), 'load_nllb_translator_for_output': MagicMock(return_value=(None,None)),
            'load_spell_corrector': MagicMock(return_value=None), '_display_frame': MagicMock(),
        }
        for name, value in replacements.items():
            mocks[name] = stack.enter_context(patch.object(host, name, value))
        stack.enter_context(patch.object(host.cv2, 'destroyAllWindows'))
        return mocks

    def test_camera_reconnect_reuses_models_and_pi_quit_exits(self):
        app, settings = self._session_fixture()
        server, clients = MagicMock(), [MagicMock(), MagicMock()]
        received = []
        def receiver(client, holder, events, recorder):
            received.append(client)
            events.put({'event':'disconnect'} if len(received) == 1 else {'cmd':'quit'})
        with contextlib.ExitStack() as stack:
            mocks = self._session_patches(stack)
            accept = stack.enter_context(patch.object(host, '_start_server_for_gui',
                side_effect=[(server,clients[0]),(server,clients[1])]))
            stack.enter_context(patch.object(host, 'receiver_loop', side_effect=receiver))
            host.run_pipeline_gui(app, settings)
        self.assertEqual(accept.call_count, 2)
        mocks['load_ocr_engine'].assert_called_once()
        mocks['load_tts'].assert_called_once()
        mocks['load_unwarper'].assert_not_called()
        mocks['load_nllb_translator_for_output'].assert_called_once()
        mocks['AudioRouter'].return_value.close.assert_called_once()
        self.assertIsNone(host.AUDIO_ROUTER)
        self.assertIsNone(host._GUI_CONTROLLER)
        self.assertFalse(app.publish_event.call_args.kwargs['error'])

    def test_document_failure_allows_another_capture(self):
        app, settings = self._session_fixture()
        image = np.zeros((20,20,3),np.uint8)
        def receiver(client, holder, events, recorder):
            holder.update(image)
            app.key_queue.put('s')
        calls = []
        def process(*args, **kwargs):
            calls.append(args[1])
            if len(calls) == 1:
                app.key_queue.put('s')
                raise RuntimeError('synthetic OCR failure')
            app.pipeline_event_queue.put({'cmd':'quit'})
        with contextlib.ExitStack() as stack:
            mocks = self._session_patches(stack)
            stack.enter_context(patch.object(host, '_start_server_for_gui', return_value=(MagicMock(), MagicMock())))
            stack.enter_context(patch.object(host, 'receiver_loop', side_effect=receiver))
            stack.enter_context(patch.object(host, 'pre_capture_quality_loop', return_value=image))
            stack.enter_context(patch.object(host, 'process_and_speak', side_effect=process))
            host.run_pipeline_gui(app, settings)
        self.assertEqual(calls, [1,2])
        mocks['load_ocr_engine'].assert_called_once()
        self.assertTrue(any('Press S to retry' in str(c) for c in app.publish_event.call_args_list))
        self.assertFalse(app.publish_event.call_args.kwargs['error'])

    def test_cancel_while_waiting_closes_listener(self):
        app, _ = self._session_fixture()
        app.key_queue = ControlQueue(); app.key_queue.put('q')
        server = MagicMock()
        with self.assertRaises(InterruptedError):
            host._start_server_for_gui(9999, app, server)
        server.close.assert_called_once()
        server.accept.assert_not_called()

    def test_missing_cached_model_fails_locally(self):
        with patch.object(host.os.path, 'isfile', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'Local model .* incomplete'):
                host._cached_paddle_model('missing-test-model', ('inference.onnx',))

    def _run_gate_sequence(self, states, distinct=True):
        keys, events = ControlQueue(), ControlQueue()
        seen = []
        good = dict(page_found=True,page_complete=True,distance_ok=True,text_readable=True,
            focus_ok=True,lighting_ok=True,row_count=20,band_lap_min=1000,
            line_lap_p20=1000,light_tile_std=5,missing_sides=[],unreadable_sides=[])
        class Clock:
            value = 100.0
            def monotonic(self): return self.value
            def time(self): return self.value
            def sleep(self, delay):
                self.value += delay
                if distinct: holder.update(np.full((10,10,3),len(seen),np.uint8))
                if len(seen) >= len(states) or self.value > 110: keys.put('q')
        class ImmediateThread:
            def __init__(self, target, args, **kwargs): self.target,self.args=target,args
            def start(self): self.target(*self.args)
        def worker(frame, sequence, timestamp, ocr, result):
            state = states[len(seen)]
            assessment = dict(good)
            if state == 'soft': assessment['distance_ok'] = False
            if state == 'hard':
                assessment.update(page_complete=False,missing_sides=['B'])
            seen.append((sequence,frame))
            result.update(assessment,frame,sequence,timestamp)
        clock = Clock()
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(host,'time',clock))
            holder = host.FrameHolder(); holder.update(np.zeros((10,10,3),np.uint8))
            stack.enter_context(patch.object(host,'threading',types.SimpleNamespace(
                Lock=threading.Lock, Thread=ImmediateThread)))
            stack.enter_context(patch.object(host,'_detection_worker',side_effect=worker))
            stack.enter_context(patch.object(host,'_estimate_capture_motion',return_value=dict(known=True,ok=True,score=0)))
            stack.enter_context(patch.object(host,'AudioGuidance',return_value=MagicMock()))
            stack.enter_context(patch.object(host,'draw_quality_overlay',side_effect=lambda image,*a:image))
            stack.enter_context(patch.object(host,'_display_frame'))
            stack.enter_context(patch.object(host,'beep_ready'))
            result = host.pre_capture_quality_loop(holder,None,None,keys,events)
        return result,seen

    def test_temporal_gate_requires_three_distinct_good_observations(self):
        result,seen = self._run_gate_sequence(['good']*3)
        self.assertEqual(len(seen),3)
        self.assertEqual(len({sequence for sequence,_ in seen}),3)
        self.assertIs(result,seen[-1][1])

    def test_temporal_gate_keeps_one_soft_failure(self):
        result,seen = self._run_gate_sequence(['good','soft','good','good'])
        self.assertEqual(len(seen),4)
        self.assertIsNotNone(result)

    def test_temporal_gate_clears_on_clipped_text(self):
        result,seen = self._run_gate_sequence(['good','good','hard','good','good','good'])
        self.assertEqual(len(seen),6)
        self.assertIsNotNone(result)

    def test_repeated_same_frame_never_satisfies_temporal_gate(self):
        result,seen = self._run_gate_sequence(['good']*3, distinct=False)
        self.assertEqual(len(seen),1)
        self.assertIsNone(result)


if __name__ == '__main__':
    unittest.main(verbosity=2)
