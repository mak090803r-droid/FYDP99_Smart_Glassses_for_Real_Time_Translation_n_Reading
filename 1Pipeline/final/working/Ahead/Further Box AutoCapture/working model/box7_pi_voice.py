"""Pi4 microphone client on its own socket/thread; video protocol is untouched."""
import threading
import socket
import time
from box7_voice_io import (VOICE_PORT, MAX_SECONDS, Recorder, send_packet,
                           receive_packet, close_socket)


class PiVoiceClient:
    def __init__(self, host, port=VOICE_PORT, ready_cue=None):
        self.host, self.port = host, port
        self.enabled = False
        self.device = "default"
        self._socket = None
        self._send_lock = threading.Lock()
        self._stop = threading.Event()
        self._end = threading.Event()
        self._held = False
        self._request = 0
        self._recording = False
        self._thread = None
        self.ready_cue = ready_cue

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True, name="pi4-voice-link")
        self._thread.start()

    def _send(self, message, pcm=b""):
        if self._socket is None:
            return False
        try:
            send_packet(self._socket, self._send_lock, message, pcm)
            return True
        except OSError:
            return False

    def begin(self):
        if not self.enabled or self._held or self._recording:
            return
        self._held = True
        self._end = threading.Event()
        self._request += 1
        self._send({"op": "begin", "request": self._request})

    def end(self):
        self._held = False
        self._end.set()

    def toggle(self):
        # Terminals have no key-release events; G toggles recording there.
        if self._held:
            self.end()
        else:
            self.begin()

    def cancel(self):
        self.end()
        self._request += 1
        self._send({"op": "cancel"})

    def _record(self, token, request):
        if token is None:
            self.end()
            print("[VOICE] Host is not ready for voice input.", flush=True)
            return
        if request != self._request or not self._held:
            self._send({"op": "cancel"})
            return
        end = self._end
        self._recording = True
        recorder = Recorder(self.device)
        try:
            recorder.start()
            if self.ready_cue is not None:
                try:
                    self.ready_cue()
                except Exception as exc:
                    print(f"[VOICE] Ready tone unavailable: {exc}", flush=True)
            print("[VOICE] LISTENING — release Capture (or press G again in terminal).", flush=True)
            self._send({"op": "listening"})
            end.wait(MAX_SECONDS)
            pcm = recorder.stop()
            self._held = False
            if request == self._request and self.enabled:
                self._send({"op": "audio", "token": token}, pcm)
        except Exception as exc:
            print(f"[VOICE] Microphone failed: {exc}", flush=True)
            self._send({"op": "error", "message": str(exc)[:1000]})
        finally:
            try:
                recorder.stop()
            except Exception:
                pass
            self._recording = False
            self._held = False

    def _loop(self):
        while not self._stop.is_set():
            sock = None
            heartbeat_stop = threading.Event()
            try:
                sock = socket.create_connection((self.host, self.port), timeout=2)
                sock.settimeout(4)
                self._socket = sock
                def heartbeat():
                    while not heartbeat_stop.wait(0.8):
                        if not self._send({"op": "ping"}):
                            return
                threading.Thread(target=heartbeat, daemon=True).start()
                while not self._stop.is_set():
                    packet, _ = receive_packet(sock)
                    op = packet.get("op")
                    if op == "config":
                        self.enabled = bool(packet.get("enabled"))
                        self.device = packet.get("device", "default")
                        if not self.enabled:
                            self.end()
                        print(f"[VOICE] {'Enabled' if self.enabled else 'Disabled'} by host GUI; source={self.device}", flush=True)
                    elif op == "ready":
                        threading.Thread(target=self._record, args=(packet.get("token"), packet.get("request")), daemon=True).start()
                    elif op == "trigger_start":
                        self.begin()
                    elif op == "trigger_end":
                        self.end()
            except (OSError, ValueError, ConnectionError):
                pass
            finally:
                self.enabled = False
                self.end()
                self._request += 1
                heartbeat_stop.set()
                close_socket(sock)
                if self._socket is sock:
                    self._socket = None
            self._stop.wait(2)

    def close(self):
        self._stop.set()
        self.cancel()
        close_socket(self._socket)
        if self._thread:
            self._thread.join(timeout=3)
