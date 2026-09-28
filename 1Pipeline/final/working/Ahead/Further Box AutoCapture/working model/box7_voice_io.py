"""Bounded microphone capture and a separate PCM input protocol for Box7/Pi4.

No recognizer dependencies. No recording occurs until start() is called.
Linux uses the configured Pulse/PipeWire source (parec), or ALSA arecord.
Bluetooth must already expose an HFP/HSP microphone; profiles are not changed
under an active playback stream.
"""
import json
import os
import shutil
import socket
import struct
import subprocess
import threading

VOICE_PORT = 10001
RATE = 16000
MAX_SECONDS = 30
MAX_BYTES = RATE * 2 * MAX_SECONDS


def linux_input_source(device):
    """Select the one exposed Bluetooth mic, or honor an explicit source.

    Never silently use a speaker-monitor source as the wearer's microphone.
    Profile changes remain an OS setup step so an existing PCM stream survives.
    """
    if device != "default" or not shutil.which("pactl"):
        return device
    result = subprocess.run(["pactl", "-f", "json", "list", "sources"], capture_output=True, text=True, timeout=3)
    if result.returncode:
        return device
    try:
        sources = json.loads(result.stdout)
    except ValueError:
        return device
    microphones = [s for s in sources if not str(s.get("name", "")).endswith(".monitor")]
    bluetooth = [s for s in microphones if "bluez" in s.get("name", "").lower()]
    if len(bluetooth) == 1:
        return bluetooth[0]["name"]
    if len(bluetooth) > 1:
        qcy = [s for s in bluetooth if "qcy" in (s.get("description", "") + str(s.get("properties", {}))).lower()]
        if len(qcy) == 1:
            return qcy[0]["name"]
        raise RuntimeError("Multiple Bluetooth microphones. Enter the QCY source name in Box7 Voice Input.")
    raise RuntimeError("No Bluetooth microphone is exposed. Select QCY's Headset/HFP profile in Pi audio settings, or enter another microphone source explicitly. Use piweb_cli4.py --voice-devices to list inputs.")


def read_exact(sock, size):
    result = bytearray()
    while len(result) < size:
        block = sock.recv(min(size - len(result), 65536))
        if not block:
            raise ConnectionError("Voice connection closed")
        result.extend(block)
    return bytes(result)


def send_packet(sock, lock, header, pcm=b""):
    if len(pcm) > MAX_BYTES:
        raise ValueError("Voice recording exceeds 30 seconds")
    data = json.dumps(dict(header, pcm_bytes=len(pcm)), allow_nan=False).encode("utf-8")
    if len(data) > 8192:
        raise ValueError("Voice header too large")
    with lock:
        sock.sendall(struct.pack("!I", len(data)) + data + pcm)


def receive_packet(sock):
    size = struct.unpack("!I", read_exact(sock, 4))[0]
    if not 0 < size <= 8192:
        raise ValueError("Invalid voice header size")
    header = json.loads(read_exact(sock, size))
    if not isinstance(header, dict):
        raise ValueError("Invalid voice header")
    count = header.get("pcm_bytes", 0)
    if type(count) is not int or not 0 <= count <= MAX_BYTES or count % 2:
        raise ValueError("Invalid voice PCM length")
    return header, read_exact(sock, count)


class Recorder:
    def __init__(self, device="default"):
        self.device = device or "default"
        self._chunks = []
        self._size = 0
        self._lock = threading.Lock()
        self._stream = self._process = self._reader = None
        self.error = ""
        self.limited = threading.Event()

    def _append(self, block):
        with self._lock:
            remaining = MAX_BYTES - self._size
            if remaining > 0:
                value = bytes(block[:remaining])
                self._chunks.append(value)
                self._size += len(value)
            if self._size >= MAX_BYTES:
                self.limited.set()

    def start(self):
        if os.name == "nt":
            import sounddevice as sd
            device = None if self.device == "default" else (int(self.device) if self.device.isdecimal() else self.device)
            def callback(data, frames, timing, status):
                if status:
                    self.error = str(status)
                self._append(data)
            self._stream = sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16",
                device=device, callback=callback, blocksize=320)
            self._stream.start()
        else:
            if shutil.which("pw-cat"):
                command = ["pw-cat", "--record", "--raw", "--format=s16",
                           "--rate=16000", "--channels=1", "--latency=40ms"]
                if self.device != "default":
                    command.append("--target=" + self.device)
                command.append("-")
            elif shutil.which("parec"):
                command = ["parec", "--raw", "--format=s16le", "--rate=16000", "--channels=1"]
                device = linux_input_source(self.device)
                if device != "default":
                    command.append("--device=" + device)
            elif shutil.which("arecord"):
                command = ["arecord", "-q", "-D", self.device, "-t", "raw", "-f", "S16_LE", "-r", "16000", "-c", "1"]
            else:
                raise RuntimeError("Install pw-cat, parec (pulseaudio-utils) or arecord (alsa-utils) on the Pi.")
            self._process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            def read():
                try:
                    while not self.limited.is_set():
                        chunk = self._process.stdout.read(640)
                        if not chunk:
                            break
                        self._append(chunk)
                except (OSError, ValueError):
                    pass
            self._reader = threading.Thread(target=read, daemon=True, name="box7-microphone")
            self._reader.start()

    def stop(self):
        if self._stream is not None:
            try:
                self._stream.stop()
            finally:
                self._stream.close()
                self._stream = None
        if self._process is not None:
            if self._process.poll() is None:
                self._process.terminate()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=2)
            if self._reader:
                self._reader.join(timeout=2)
            error = self._process.stderr.read().decode("utf-8", errors="replace").strip()
            if error:
                self.error = error[-500:]
            self._process.stdout.close()
            self._process.stderr.close()
            self._process = None
        with self._lock:
            pcm = b"".join(self._chunks)
        if not pcm and self.error:
            raise RuntimeError(self.error)
        return pcm


def close_socket(sock):
    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        sock.close()
