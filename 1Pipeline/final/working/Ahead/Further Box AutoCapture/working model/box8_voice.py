"""Optional voice service. Heavy ASR is isolated in an on-demand CPU process."""
import base64
import json
import os
from pathlib import Path
import queue
import socket
import subprocess
import threading
import time
from box8_commands import parse_command
from box7_voice_io import Recorder, VOICE_PORT, MAX_SECONDS, send_packet, receive_packet, close_socket


class WhisperProcess:
    def __init__(self, root):
        self.root = Path(root)
        self.process = None
        self._lock = threading.Lock()

    def paths(self):
        default = self.root / ".venv_box7_voice" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        return (Path(os.environ.get("FYDP_VOICE_PYTHON", str(default))),
                Path(os.environ.get("FYDP_WHISPER_MODEL", str(self.root / "box7_models/faster-whisper-small.en"))))

    def check(self):
        python, model = self.paths()
        if not python.is_file() or not (model / "model.bin").is_file():
            raise RuntimeError("Voice assets missing. Follow BOX7_README.md one-time local setup.")

    def transcribe(self, pcm, cancelled=None):
        with self._lock:
            if cancelled is not None and cancelled.is_set():
                raise RuntimeError("Voice request cancelled")
            self.check()
            if self.process is None or self.process.poll() is not None:
                python, model = self.paths()
                self.process = subprocess.Popen([str(python), "-u", str(self.root / "box7_whisper_worker.py"), str(model)],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True, encoding="utf-8", bufsize=1,
                    creationflags=(subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS) if os.name == "nt" else 0)
            if cancelled is not None and cancelled.is_set():
                self.close()
                raise RuntimeError("Voice request cancelled")
            process = self.process
            process.stdin.write(json.dumps({"pcm": base64.b64encode(pcm).decode("ascii")}) + "\n")
            process.stdin.flush()
            line = process.stdout.readline()
            if not line:
                raise RuntimeError("Whisper worker exited. Check the separate voice environment and local model.")
            result = json.loads(line)
            if result.get("error"):
                raise RuntimeError(result["error"])
            return result

    def close(self):
        # Do not wait for the transcription lock: cancellation must interrupt
        # inference before the main pipeline starts a new capture.
        process, self.process = self.process, None
        if process is not None:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
            for pipe in (process.stdin, process.stdout):
                try:
                    pipe.close()
                except (OSError, ValueError):
                    pass


class VoiceService:
    """context() returns ordinary Python data; emit() enqueues Tk events only."""
    def __init__(self, root, context, emit, port=VOICE_PORT, recognizer=None):
        self.context, self.emit, self.port = context, emit, port
        self.recognizer = recognizer or WhisperProcess(root)
        self.enabled = False
        self.busy = False
        self.source = "Pi earbuds"
        self.device = "default"
        self._lock = threading.RLock()
        self._generation = 0
        self._commands = queue.Queue(maxsize=4)
        self._paused = []
        self._recorder = None
        self._record_end = threading.Event()
        self._asr_cancel = threading.Event()
        self._server = self._peer = None
        self._peer_lock = threading.Lock()
        self._stop = threading.Event()
        self._timer = None

    def status(self, message, **data):
        self.emit("voice", message=message, enabled=self.enabled, busy=self.busy, **data)

    def configure(self, enabled, source="Pi earbuds", device="default"):
        self.source, self.device = source, device or "default"
        if not enabled:
            self.enabled = False
            self.cancel()
            self._stop.set()
            self._send({"op": "config", "enabled": False})
            close_socket(self._peer)
            close_socket(self._server)
            self._peer = self._server = None
            self.status("Voice disabled; recognizer unloaded")
            return
        self.recognizer.check()
        if self.enabled:
            self._send({"op": "config", "enabled": True, "device": self.device})
            return
        self.enabled = True
        self._stop = threading.Event()
        try:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("0.0.0.0", self.port))
            server.listen(1)
            server.settimeout(0.5)
            self._server = server
            self.port = server.getsockname()[1]
        except Exception:
            server.close()
            self.enabled = False
            raise
        threading.Thread(target=self._serve, args=(server, self._stop), daemon=True, name="box7-voice-link").start()
        self.status("Voice enabled. Hold G or hold Capture for 0.7 s; wait for LISTENING.")

    def _send(self, header):
        peer = self._peer
        if peer is None:
            return False
        try:
            send_packet(peer, self._peer_lock, header)
            return True
        except OSError:
            return False

    def _serve(self, server, stop):
        while not stop.is_set():
            peer = None
            try:
                peer, _ = server.accept()
                peer.settimeout(2.0)
                self._peer = peer
                send_packet(peer, self._peer_lock, {"op": "config", "enabled": True, "device": self.device})
                self.status("Pi voice channel connected", pi_voice=True)
                while not stop.is_set():
                    try:
                        packet, pcm = receive_packet(peer)
                    except socket.timeout:
                        # Pi sends a ping every second; a timed-out partial
                        # packet is never resumed as if it were a new header.
                        raise ConnectionError("Voice heartbeat timed out")
                    op = packet.get("op")
                    if op == "ping":
                        send_packet(peer, self._peer_lock, {"op": "pong"})
                    elif op == "begin":
                        token = self.begin()
                        send_packet(peer, self._peer_lock, {"op": "ready", "token": token, "request": packet.get("request")})
                    elif op == "audio":
                        self.submit(pcm, packet.get("token"))
                    elif op == "cancel":
                        self.cancel()
                    elif op == "error":
                        self.cancel()
                        self.status("Pi microphone: " + str(packet.get("message", "unavailable")))
                    elif op == "listening":
                        self.status("LISTENING on Pi earbuds — release to submit")
            except socket.timeout:
                continue
            except (OSError, ValueError, ConnectionError) as exc:
                if not stop.is_set() and peer is not None:
                    self.cancel()
                    self.status(f"Pi voice disconnected: {exc}", pi_voice=False)
            finally:
                if peer is not None:
                    close_socket(peer)
                    if self._peer is peer:
                        self._peer = None

    def begin(self):
        with self._lock:
            phase, modules = self.context()
            if not self.enabled:
                self.status("Enable Voice Input first")
                return None
            if self.busy or phase not in ("camera", "audio"):
                self.status("Wait for the current operation to finish before speaking")
                return None
            self._generation += 1
            token = self._generation
            self._asr_cancel = threading.Event()
            self.busy = True
            self._paused = [(m, bool(m.is_paused())) for m in modules if m.is_speaking()]
            for module, _ in self._paused:
                module.pause()
            self._timer = threading.Timer(MAX_SECONDS + 3, lambda: self._timeout(token))
            self._timer.daemon = True
            self._timer.start()
            self.status("Preparing microphone; wait for LISTENING")
            return token

    def _timeout(self, token):
        if token == self._generation and self.busy:
            self.cancel()
            self.status("Voice timed out; hold G or Capture to try again")

    def start_input(self):
        if self.source == "Pi earbuds":
            if not self.enabled or not self._send({"op": "trigger_start"}):
                self.status("Pi microphone unavailable. Run piweb_cli4.py and enable Voice Input.")
            return
        token = self.begin()
        if token is None:
            return
        end = self._record_end = threading.Event()
        def record():
            recorder = Recorder(self.device)
            try:
                recorder.start()
                with self._lock:
                    if token != self._generation:
                        recorder.stop()
                        return
                    self._recorder = recorder
                self.status("LISTENING on PC — release G/Capture to submit")
                end.wait(MAX_SECONDS)
                pcm = recorder.stop()
                self._recorder = None
                self.submit(pcm, token)
            except Exception as exc:
                try:
                    recorder.stop()
                except Exception:
                    pass
                if token == self._generation:
                    self.cancel()
                    self.status(f"Microphone unavailable: {exc}")
        threading.Thread(target=record, daemon=True, name="box7-pc-recording").start()

    def end_input(self):
        if self.source == "Pi earbuds":
            self._send({"op": "trigger_end"})
        else:
            self._record_end.set()

    def submit(self, pcm, token):
        with self._lock:
            if token is None or token != self._generation or not self.enabled or not self.busy:
                return False
            if self._timer:
                self._timer.cancel()
            if len(pcm) < 6400:
                self.cancel()
                self.status("Recording too short; hold until LISTENING and speak")
                return False
            self._timer = threading.Timer(60, lambda: self._timeout(token))
            self._timer.daemon = True
            self._timer.start()
            self.status("Recognizing speech locally")
        def recognize():
            try:
                if token != self._generation or not self.enabled:
                    return
                result = self.recognizer.transcribe(pcm, cancelled=cancellation)
                with self._lock:
                    if token != self._generation or not self.enabled:
                        return
                    command = parse_command(result.get("text", ""))
                    self.status("Heard: " + (command.text or "[no speech]"), transcript=command.text,
                                action=command.action, asr_seconds=result.get("seconds"))
                    if command.action in ("unknown", "cancel"):
                        self.finish_command(restore=True)
                        self.status("Command not recognized; say 'voice help'" if command.action == "unknown" else "Voice cancelled")
                    else:
                        self._commands.put_nowait(command)
            except Exception as exc:
                if token == self._generation:
                    self.cancel()
                    self.status(f"Voice recognition failed: {exc}")
        cancellation = self._asr_cancel
        threading.Thread(target=recognize, daemon=True, name="box7-recognition").start()
        return True

    def take_command(self):
        try:
            return self._commands.get_nowait()
        except queue.Empty:
            return None

    def finish_command(self, restore=False):
        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None
            if restore:
                for module, was_paused in self._paused:
                    if not was_paused and module.is_speaking():
                        module.resume()
            self._paused = []
            self.busy = False

    def cancel(self, restore=True):
        with self._lock:
            self._generation += 1
            self._asr_cancel.set()
            self._record_end.set()
            self.finish_command(restore)
            while self.take_command() is not None:
                pass
        self.recognizer.close()

    def close(self):
        self.configure(False)
