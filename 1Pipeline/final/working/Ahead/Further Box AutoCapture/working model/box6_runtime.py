"""Small, dependency-light control and legacy camera-protocol helpers for Box6."""

import io
import json
import pickle
import queue
import struct
import threading
import time

import cv2
import numpy as np


class ControlQueue(queue.Queue):
    """Latch terminal events even when a capture/playback consumer drains them."""

    def __init__(self):
        super().__init__()
        self.disconnected = threading.Event()
        self.quit_requested = threading.Event()

    def put(self, item, block=True, timeout=None):
        action = control_action(item)
        if action == "disconnect":
            self.disconnected.set()
        if action == "quit":
            self.quit_requested.set()
        super().put(item, block, timeout)


def control_action(item):
    if isinstance(item, str):
        return {"s": "capture", "a": "toggle_pause", "q": "quit",
                "n": "next", "p": "previous", "r": "repeat"}.get(item.lower())
    if not isinstance(item, dict):
        return None
    if item.get("event") == "disconnect":
        return "disconnect"
    cmd = item.get("cmd")
    aliases = {
        "capture_from_pi": "capture", "gui_stop_audio": "stop",
        "quit": "quit", "gui_toggle_pause": "toggle_pause",
        "tts_pause_toggle_from_pi": "toggle_pause",
        "gui_next": "next", "gui_previous": "previous",
        "gui_repeat": "repeat", "audio_route_changed": "repeat",
    }
    if cmd in aliases:
        return aliases[cmd]
    if cmd == "tts_control" or item.get("resp") == "tts_control":
        action = item.get("action")
        return action if action in {
            "pause", "resume", "toggle_pause", "stop", "next", "previous",
            "repeat", "quit"} else None
    return None


def terminal_action(*queues):
    for q in queues:
        if q is not None and getattr(q, "quit_requested", threading.Event()).is_set():
            return "quit"
        if q is not None and getattr(q, "disconnected", threading.Event()).is_set():
            return "disconnect"
    return None


def pending_actions(*queues):
    """Consume known controls; terminal state remains latched on ControlQueue."""
    actions = []
    for q in queues:
        if q is None:
            continue
        while True:
            try:
                item = q.get_nowait()
            except queue.Empty:
                break
            action = control_action(item)
            if action is not None:
                actions.append(action)
    terminal = terminal_action(*queues)
    if terminal:
        return [terminal]
    return actions


def capture_action(key_queue, event_queue):
    """In capture mode S forces a fresh frame; Stop Audio cancels monitoring."""
    result = None
    for q in (key_queue, event_queue):
        if q is None:
            continue
        while True:
            try:
                item = q.get_nowait()
            except queue.Empty:
                break
            action = control_action(item)
            if action in ("quit", "disconnect"):
                return action
            if item == "s" or (isinstance(item, dict) and item.get("cmd") == "capture_from_pi"):
                result = "force"
            elif action == "stop":
                result = "cancel"
    return terminal_action(key_queue, event_queue) or result


class PlaybackControls:
    """Consistent controls across paragraph boundaries and synthesis waits."""

    def __init__(self):
        self.last_toggle = 0.0

    def poll(self, tts, key_queue, event_queue):
        actions = pending_actions(key_queue, event_queue)
        for terminal in ("quit", "disconnect", "stop", "capture"):
            if terminal in actions:
                tts.stop()
                return "stop" if terminal == "capture" else terminal
        for action in reversed(actions):
            if action in ("next", "previous", "repeat"):
                tts.stop()
                return action
        for action in actions:
            if action == "toggle_pause":
                now = time.monotonic()
                elapsed = now - self.last_toggle
                self.last_toggle = now
                if elapsed < 0.35:
                    continue
                action = "resume" if tts.is_paused() else "pause"
            if action == "pause":
                tts.pause()
            elif action == "resume":
                tts.resume()
        return None


class _JPEGUnpickler(pickle.Unpickler):
    """Read the legacy NumPy JPEG buffer without accepting arbitrary globals."""

    def find_class(self, module, name):
        if module == "numpy" and name in ("ndarray", "dtype"):
            return getattr(np, name)
        if module in ("numpy.core.multiarray", "numpy._core.multiarray") and name == "_reconstruct":
            return np.core.multiarray._reconstruct
        if module in ("numpy.core.numeric", "numpy._core.numeric") and name == "_frombuffer":
            return np.core.numeric._frombuffer
        raise ValueError("Camera message contains an unsupported pickle type")


def recv_exact(sock, size):
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(min(size - len(data), 65536))
        if not chunk:
            raise ConnectionError("Camera connection closed")
        data.extend(chunk)
    return bytes(data)


def recv_camera_message(sock):
    kind = recv_exact(sock, 1)[0]
    if kind == 1:
        size = struct.unpack("!I", recv_exact(sock, 4))[0]
        if not 0 < size <= 65536:
            raise ValueError("Invalid camera control message length")
        payload = json.loads(recv_exact(sock, size).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Camera control must be a JSON object")
        return kind, payload
    if kind != 2:
        raise ValueError(f"Unknown camera message type: {kind}")
    size = struct.unpack("!Q", recv_exact(sock, 8))[0]
    if not 0 < size <= 16 * 1024 * 1024:
        raise ValueError("Invalid camera frame length")
    payload = recv_exact(sock, size)
    if payload.startswith(b"\xff\xd8"):
        encoded = np.frombuffer(payload, dtype=np.uint8)
    else:
        encoded = _JPEGUnpickler(io.BytesIO(payload)).load()
    if not isinstance(encoded, np.ndarray) or encoded.dtype != np.uint8 or encoded.ndim not in (1, 2):
        raise ValueError("Camera payload is not an encoded uint8 JPEG buffer")
    if encoded.size > 16 * 1024 * 1024:
        raise ValueError("Encoded camera image exceeds limit")
    frame = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    if frame is None or frame.size == 0:
        raise ValueError("Camera JPEG could not be decoded")
    if frame.shape[0] > 4320 or frame.shape[1] > 7680:
        raise ValueError("Unsupported camera image dimensions")
    return kind, frame
