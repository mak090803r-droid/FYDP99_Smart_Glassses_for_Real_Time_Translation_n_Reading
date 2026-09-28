"""Local PCM routing and the separate Box6/Pi3 audio channel.

Copy this module beside piweb_cli3.py on the Pi. No models run on the Pi.
Camera traffic remains on port 9999; framed JSON + signed 16-bit PCM uses
port 10000. No pickle, downloads, or additional Python packages are required.
"""

import collections
import json
import os
import queue
import shutil
import socket
import struct
import subprocess
import threading
import time
import uuid

import numpy as np


PROTOCOL_VERSION = 1
AUDIO_PORT = 10000
MAX_HEADER_BYTES = 8192
MAX_PCM_BYTES = 32 * 1024 * 1024
MAX_AUDIO_SECONDS = 180
CONTROL_ACTIONS = frozenset(
    ("next", "previous", "repeat", "pause", "resume", "stop", "toggle_pause"))


def _read_exact(sock, length):
    chunks = bytearray()
    while len(chunks) < length:
        part = sock.recv(length - len(chunks))
        if not part:
            raise ConnectionError("Pi audio connection closed")
        chunks.extend(part)
    return bytes(chunks)


def _send_packet(sock, lock, message, pcm=b""):
    if len(pcm) > MAX_PCM_BYTES:
        raise ValueError("Audio exceeds the bounded PCM packet size")
    header = dict(message, pcm_bytes=len(pcm))
    encoded = json.dumps(header, separators=(",", ":"), allow_nan=False).encode()
    if len(encoded) > MAX_HEADER_BYTES:
        raise ValueError("Audio header is too large")
    with lock:
        sock.sendall(struct.pack("!I", len(encoded)) + encoded + pcm)


def _recv_packet(sock):
    size = struct.unpack("!I", _read_exact(sock, 4))[0]
    if not 0 < size <= MAX_HEADER_BYTES:
        raise ValueError("Invalid audio header length")
    message = json.loads(_read_exact(sock, size).decode("utf-8"))
    if not isinstance(message, dict):
        raise ValueError("Audio header must be an object")
    pcm_size = message.get("pcm_bytes", 0)
    if type(pcm_size) is not int or not 0 <= pcm_size <= MAX_PCM_BYTES:
        raise ValueError("Invalid audio payload length")
    return message, _read_exact(sock, pcm_size)


def _as_pcm(audio):
    data = np.asarray(audio)
    if data.ndim == 1:
        data = data.reshape(-1, 1)
    if data.ndim != 2 or data.shape[1] not in (1, 2):
        raise ValueError("Audio must be mono or stereo")
    if np.issubdtype(data.dtype, np.floating):
        data = np.nan_to_num(data, nan=0.0, posinf=1.0, neginf=-1.0)
        data = np.rint(np.clip(data, -1.0, 1.0) * 32767.0)
    else:
        data = np.clip(data, -32768, 32767)
    return np.ascontiguousarray(data, dtype="<i2")


class PlaybackState:
    def __init__(self, speed=1.0):
        self.cancel = threading.Event()
        self.paused = threading.Event()
        self.speed = max(0.5, min(3.0, float(speed)))

    def control(self, action, speed=None):
        if action == "stop":
            self.cancel.set()
            self.paused.clear()
        elif action == "pause":
            self.paused.set()
        elif action == "resume":
            self.paused.clear()
        elif action == "toggle_pause":
            if self.paused.is_set():
                self.paused.clear()
            else:
                self.paused.set()
        elif action == "speed" and speed is not None:
            self.speed = max(0.5, min(3.0, float(speed)))


class PCMPlayer:
    """20 ms output blocks; pause retains the exact unsent sample position.

    A fixed 48 kHz device stream supports speed changes without requiring the
    sound card to accept unusual sample rates. The baseline rate-based speed
    behaviour (including its pitch change) is preserved.
    """
    def __init__(self, device=None, allow_aplay=False):
        self.device = device
        self.allow_aplay = allow_aplay

    def available(self):
        try:
            import sounddevice as sd
            sd.check_output_settings(
                device=self.device, channels=1, dtype="int16", samplerate=48000)
            return "sounddevice"
        except Exception:
            if self.allow_aplay and shutil.which("aplay"):
                return "aplay"
        return None

    def play(self, pcm, sample_rate, state):
        if not len(pcm) or state.cancel.is_set():
            return
        channels = pcm.shape[1]
        output_rate = 48000
        block_size = 960
        stream = None
        process = None
        process_watch_done = threading.Event()
        try:
            try:
                import sounddevice as sd
                stream = sd.RawOutputStream(
                    samplerate=output_rate, channels=channels, dtype="int16",
                    blocksize=block_size, latency="low", device=self.device)
                stream.start()
            except Exception:
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass
                    stream = None
                if not self.allow_aplay or not shutil.which("aplay"):
                    raise RuntimeError(
                        "No usable audio output. Select a playback device "
                        "or enable the Pi's ALSA output.")
                args = ["aplay", "-q", "-t", "raw", "-f", "S16_LE",
                        "-c", str(channels), "-r", str(output_rate),
                        "--buffer-time=80000", "--period-time=20000"]
                if isinstance(self.device, str) and self.device:
                    args += ["-D", self.device]
                process = subprocess.Popen(
                    args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, bufsize=0)

                def watch_cancel():
                    while not process_watch_done.wait(0.02):
                        if state.cancel.is_set():
                            if process.poll() is None:
                                process.terminate()
                            return
                threading.Thread(
                    target=watch_cancel, daemon=True,
                    name="pi-aplay-cancel").start()
            position = 0.0
            while position < len(pcm) and not state.cancel.is_set():
                if state.paused.is_set():
                    state.cancel.wait(0.02)
                    continue
                step = sample_rate * state.speed / output_rate
                indices = position + np.arange(block_size) * step
                indices = indices[indices < len(pcm)]
                if not len(indices):
                    break
                left = indices.astype(np.int64)
                right = np.minimum(left + 1, len(pcm) - 1)
                fraction = (indices - left)[:, None]
                output = np.rint(
                    pcm[left] * (1.0 - fraction) + pcm[right] * fraction
                ).astype("<i2").tobytes()
                if stream is not None:
                    stream.write(output)
                else:
                    remaining = memoryview(output)
                    while remaining and not state.cancel.is_set():
                        count = process.stdin.write(remaining)
                        if not count:
                            raise RuntimeError("Pi ALSA output stopped")
                        remaining = remaining[count:]
                position += len(indices) * step
            if stream is not None:
                if state.cancel.is_set():
                    stream.abort()
                else:
                    stream.stop()  # Completion follows actual device drain.
            if process is not None:
                try:
                    process.stdin.close()
                except (OSError, BrokenPipeError):
                    pass
                code = process.wait(timeout=3.0)
                if code and not state.cancel.is_set():
                    raise RuntimeError("Pi ALSA playback failed; check its output device")
        except (BrokenPipeError, OSError):
            if not state.cancel.is_set():
                raise
        finally:
            process_watch_done.set()
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()


class _Ticket:
    def __init__(self):
        self.done = threading.Event()
        self.status = None
        self.error = None


class RemoteAudioServer:
    def __init__(self, port=AUDIO_PORT, host="0.0.0.0", event_callback=None):
        self.host, self.port = host, port
        self.event_callback = event_callback
        self._closed = threading.Event()
        self._lock = threading.RLock()
        self._send_lock = threading.Lock()
        self._listener = None
        self._socket = None
        self._peer = None
        self._expected_peer = None
        self._pending = {}
        self._started = False
        self._accept_thread = None
        self._handshake_socket = None

    def _emit(self, event, **payload):
        if self.event_callback:
            try:
                self.event_callback(event, **payload)
            except Exception:
                pass

    @property
    def connected(self):
        with self._lock:
            return self._socket is not None

    def set_expected_peer(self, address):
        with self._lock:
            self._expected_peer = address
            sock = self._socket if self._peer != address else None
        if sock is not None:
            self._disconnect(sock, "Audio peer differs from the connected camera")

    def start(self):
        with self._lock:
            if self._started:
                return
            self._started = True
            self._accept_thread = threading.Thread(
                target=self._accept_loop, daemon=True,
                name="box6-audio-listener")
            self._accept_thread.start()

    def _accept_loop(self):
        if self._closed.is_set():
            return
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((self.host, self.port))
            self.port = listener.getsockname()[1]
            listener.listen(2)
            listener.settimeout(0.5)
            self._listener = listener
            while not self._closed.is_set():
                try:
                    sock, address = listener.accept()
                except socket.timeout:
                    continue
                try:
                    with self._lock:
                        self._handshake_socket = sock
                        if self._closed.is_set():
                            raise OSError("Audio server closing")
                    sock.settimeout(4)
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    message, pcm = _recv_packet(sock)
                    if (pcm or message.get("type") != "hello" or
                            message.get("protocol") != PROTOCOL_VERSION or
                            not message.get("audio_ready")):
                        raise ValueError("Pi audio handshake rejected: no usable output")
                    with self._lock:
                        if self._expected_peer and address[0] != self._expected_peer:
                            raise ValueError("Pi audio peer does not match camera peer")
                        old = self._socket
                    if old is not None:
                        self._disconnect(old, "Pi audio connection replaced")
                    _send_packet(sock, self._send_lock, {
                        "type": "hello", "protocol": PROTOCOL_VERSION})
                    # Individual receives have a deadline; heartbeats prevent
                    # a paused or idle connection being mistaken for a stall.
                    sock.settimeout(8)
                    with self._lock:
                        if self._closed.is_set():
                            raise OSError("Audio server closing")
                        self._socket = sock
                        self._peer = address[0]
                    self._emit("audio_route", connected=True,
                               message=f"Pi audio connected ({address[0]})")
                    threading.Thread(
                        target=self._receive_loop, args=(sock,), daemon=True,
                        name="box6-audio-receiver").start()
                except Exception as exc:
                    sock.close()
                    if not self._closed.is_set():
                        self._emit("audio_error", message=str(exc))
                finally:
                    with self._lock:
                        self._handshake_socket = None
        except OSError as exc:
            if not self._closed.is_set():
                self._emit("audio_error", message=f"Pi audio listener: {exc}")
        finally:
            listener.close()
            self._listener = None

    def _receive_loop(self, sock):
        try:
            while not self._closed.is_set():
                message, pcm = _recv_packet(sock)
                if pcm:
                    raise ValueError("Pi must not send PCM to the host")
                kind = message.get("type")
                if kind == "ping":
                    _send_packet(sock, self._send_lock, {"type": "pong"})
                elif kind == "done":
                    with self._lock:
                        ticket = self._pending.get(message.get("id"))
                    if ticket:
                        ticket.status = message.get("status")
                        ticket.error = message.get("error")
                        ticket.done.set()
                elif kind == "control" and message.get("action") in CONTROL_ACTIONS:
                    self._emit("pi_control", action=message["action"])
                elif kind not in ("started", "paused", "resumed", "pong"):
                    raise ValueError("Unsupported Pi audio response")
        except Exception as exc:
            self._disconnect(sock, str(exc))

    def _disconnect(self, sock, reason):
        with self._lock:
            is_current = self._socket is sock
            if is_current:
                self._socket = None
                self._peer = None
                for ticket in self._pending.values():
                    ticket.error = "Pi audio disconnected: " + reason
                    ticket.status = "error"
                    ticket.done.set()
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        sock.close()
        if is_current:
            self._emit("audio_route", connected=False, message=reason)

    def play(self, pcm, sample_rate, state, connect_timeout=0.0):
        deadline = time.monotonic() + max(0, connect_timeout)
        while not self.connected and time.monotonic() < deadline:
            if state.cancel.wait(0.05):
                return
        if state.cancel.is_set():
            return
        with self._lock:
            sock = self._socket
            if sock is None:
                raise RuntimeError(
                    "Audio(Pi) selected but Pi3 audio is disconnected. "
                    "Start piweb_cli3.py or uncheck Audio(Pi).")
            utterance_id = uuid.uuid4().hex
            ticket = _Ticket()
            self._pending[utterance_id] = ticket
        try:
            _send_packet(sock, self._send_lock, {
                "type": "play", "id": utterance_id, "sample_rate": sample_rate,
                "channels": pcm.shape[1], "speed": state.speed,
                "paused": state.paused.is_set(),
            }, pcm.tobytes())
            last_paused = state.paused.is_set()
            last_speed = state.speed
            running_time = 0.0
            previous = time.monotonic()
            limit = len(pcm) / sample_rate * 2.5 + 15.0
            while not ticket.done.wait(0.02):
                now = time.monotonic()
                if not state.paused.is_set():
                    running_time += now - previous
                previous = now
                if state.cancel.is_set():
                    _send_packet(sock, self._send_lock, {
                        "type": "control", "id": utterance_id, "action": "stop"})
                    if not ticket.done.wait(2):
                        self._disconnect(sock, "Pi did not acknowledge stopping audio")
                    return
                if state.paused.is_set() != last_paused:
                    last_paused = state.paused.is_set()
                    _send_packet(sock, self._send_lock, {
                        "type": "control", "id": utterance_id,
                        "action": "pause" if last_paused else "resume"})
                if state.speed != last_speed:
                    last_speed = state.speed
                    _send_packet(sock, self._send_lock, {
                        "type": "control", "id": utterance_id,
                        "action": "speed", "speed": last_speed})
                if running_time > limit:
                    self._disconnect(sock, "Pi playback completion timed out")
                    raise TimeoutError("Pi did not confirm audio completion")
            if ticket.status == "error":
                raise RuntimeError(ticket.error or "Pi audio playback failed")
            if ticket.status not in ("ok", "stopped"):
                raise RuntimeError("Invalid Pi audio completion status")
        except (ConnectionError, OSError) as exc:
            self._disconnect(sock, str(exc))
            if not state.cancel.is_set():
                raise
        finally:
            with self._lock:
                self._pending.pop(utterance_id, None)

    def close(self):
        self._closed.set()
        with self._lock:
            sock = self._socket
            listener = self._listener
            handshake = self._handshake_socket
        if sock is not None:
            self._disconnect(sock, "Host audio closed")
        for pending in (handshake, listener):
            if pending is not None:
                try:
                    pending.close()
                except OSError:
                    pass
        if self._accept_thread is not None and self._accept_thread is not threading.current_thread():
            self._accept_thread.join(timeout=1.0)


class AudioRouter:
    """One exclusive audio sink for both voices, guidance and short tones."""
    def __init__(self, port=AUDIO_PORT, event_callback=None, local_player=None,
                 connect_timeout=0.0):
        self.event_callback = event_callback
        self.server = RemoteAudioServer(port=port, event_callback=self._emit)
        self.local_player = local_player or PCMPlayer()
        self.connect_timeout = connect_timeout
        self._pi_enabled = False
        self._play_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._active = None
        self._route_generation = 0
        self._wrappers = []

    def _emit(self, event, **payload):
        if event == "audio_route":
            payload.setdefault("enabled", self._pi_enabled)
        if self.event_callback:
            try:
                self.event_callback(event, **payload)
            except Exception:
                pass

    @property
    def connected(self):
        return self.server.connected

    @property
    def pi_enabled(self):
        return self._pi_enabled

    @property
    def status(self):
        if not self._pi_enabled:
            return "PC audio"
        return "Pi audio connected" if self.connected else "Pi audio disconnected"

    def start(self):
        self.server.start()

    def set_expected_peer(self, address):
        self.server.set_expected_peer(address)

    def set_pi_enabled(self, enabled):
        enabled = bool(enabled)
        if enabled != self._pi_enabled:
            with self._state_lock:
                self._pi_enabled = enabled
                self._route_generation += 1
            self.stop()
            self._emit("audio_route", enabled=enabled, connected=self.connected,
                       message=self.status, route_changed=True)
        return self.connected or not enabled

    def wrap_tts(self, module):
        if isinstance(module, RoutedTTS):
            return module
        for wrapper in self._wrappers:
            if wrapper.module is module:
                return wrapper
        wrapper = RoutedTTS(module, self)
        self._wrappers.append(wrapper)
        return wrapper

    def play_samples(self, audio, sample_rate, state=None, *,
                     cancel_event=None, paused_event=None,
                     speed_provider=None, connect_timeout=None):
        pcm = _as_pcm(audio)
        sample_rate = int(sample_rate)
        if not 8000 <= sample_rate <= 96000:
            raise ValueError("Unsupported audio sample rate")
        if pcm.nbytes > MAX_PCM_BYTES or len(pcm) / sample_rate > MAX_AUDIO_SECONDS:
            raise ValueError("Audio clip exceeds the 180 second playback limit")
        state = state or PlaybackState()
        if cancel_event is not None:
            state.cancel = cancel_event
        if paused_event is not None:
            state.paused = paused_event
        if speed_provider is not None:
            state.speed = float(speed_provider())
        with self._state_lock:
            generation = self._route_generation
        with self._play_lock:
            with self._state_lock:
                if generation != self._route_generation or state.cancel.is_set():
                    return
                self._active = state
                pi_enabled = self._pi_enabled
            try:
                if pi_enabled:
                    self.server.play(
                        pcm, sample_rate, state,
                        self.connect_timeout if connect_timeout is None else connect_timeout)
                else:
                    self.local_player.play(pcm, sample_rate, state)
            finally:
                with self._state_lock:
                    if self._active is state:
                        self._active = None

    def stop(self):
        with self._state_lock:
            self._route_generation += 1
            if self._active:
                self._active.control("stop")
        for wrapper in self._wrappers:
            wrapper.stop()

    def close(self):
        self.stop()
        for wrapper in self._wrappers:
            wrapper.close()
        self.server.close()


class RoutedTTS:
    """Piper-compatible worker with cancellation safe across slow synthesis."""
    def __init__(self, module, router):
        self.module, self.router = module, router
        self._queue = queue.Queue(maxsize=8)
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._synthesis_lock = threading.Lock()
        self._generation = 0
        self._outstanding = 0
        self._active = None
        self._speed = getattr(module, "get_speed", lambda: 1.0)()
        self._paused = False
        self.last_error = None
        self._closed = False
        # Small synthesis cache helps repeat, and has a strict byte cap.
        self._cache = collections.OrderedDict()
        self._cache_bytes = 0
        self._prefetch_queue = queue.Queue(maxsize=1)
        self._worker_thread = threading.Thread(
            target=self._worker, daemon=True, name="box6-piper-output")
        self._worker_thread.start()
        threading.Thread(target=self._prefetch_worker, daemon=True,
                         name="box6-piper-prefetch").start()

    def __getattr__(self, name):
        return getattr(self.module, name)

    def _synthesize(self, text):
        # Cached guidance uses this API too. Keep the voice's inference serial.
        with self._synthesis_lock:
            return self.module._synthesize(text)

    def _pcm_for_text(self, text):
        with self._synthesis_lock:
            if text in self._cache:
                self._cache.move_to_end(text)
                return self._cache[text]
            chunks = self.module._synthesize(text)
            if not chunks:
                return np.empty((0, 1), dtype="<i2"), 22050
            sr = int(chunks[0].sample_rate)
            channels = int(chunks[0].sample_channels)
            if any(int(c.sample_rate) != sr or int(c.sample_channels) != channels
                   for c in chunks):
                raise RuntimeError("Piper returned inconsistent audio formats")
            pcm = np.concatenate([
                np.asarray(c.audio_int16_array, dtype="<i2").reshape(-1, channels)
                for c in chunks], axis=0)
            # Preserve the baseline's initial DAC/headset wake-up allowance.
            pcm = np.concatenate(
                [np.zeros((int(sr * 0.4), channels), dtype="<i2"), pcm], axis=0)
            if pcm.nbytes <= 16 * 1024 * 1024:
                while self._cache and (
                        self._cache_bytes + pcm.nbytes > 16 * 1024 * 1024 or
                        len(self._cache) >= 8):
                    _, (old, _) = self._cache.popitem(last=False)
                    self._cache_bytes -= old.nbytes
                self._cache[text] = (pcm, sr)
                self._cache_bytes += pcm.nbytes
            return pcm, sr

    def speak(self, text):
        text = str(text).strip() if text else ""
        if not text:
            return
        with self._condition:
            if self._closed:
                raise RuntimeError("Audio worker is closed")
            self._paused = False
            self.last_error = None
            self._outstanding += 1
            try:
                self._queue.put_nowait((self._generation, text))
            except queue.Full:
                self._outstanding -= 1
                raise RuntimeError("Speech queue full; stop or wait for playback")
            self._condition.notify_all()

    announce = speak

    def prefetch(self, text):
        """Prepare at most one upcoming segment while current audio plays."""
        text = str(text).strip() if text else ""
        if not text:
            return False
        with self._lock:
            if self._closed:
                return False
            item = (self._generation, text)
        try:
            self._prefetch_queue.put_nowait(item)
            return True
        except queue.Full:
            return False

    def _prefetch_worker(self):
        while True:
            item = self._prefetch_queue.get()
            try:
                if item is None:
                    return
                generation, text = item
                with self._lock:
                    if self._closed or generation != self._generation:
                        continue
                self._pcm_for_text(text)
            except Exception:
                # Prefetch is only an optimization; real playback will retry
                # synthesis and surface any persistent error to the caller.
                pass
            finally:
                self._prefetch_queue.task_done()

    def _worker(self):
        while True:
            item = self._queue.get()
            if item is None:
                self._queue.task_done()
                return
            generation, text = item
            state = None
            try:
                with self._condition:
                    if generation != self._generation:
                        continue
                    state = PlaybackState(self._speed)
                    if self._paused:
                        state.paused.set()
                    self._active = state
                pcm, sample_rate = self._pcm_for_text(text)
                # A stop during synthesis must not resurrect old speech when
                # a new paragraph is queued.
                with self._condition:
                    if generation != self._generation or state.cancel.is_set():
                        continue
                self.router.play_samples(pcm, sample_rate, state)
            except Exception as exc:
                with self._condition:
                    if generation == self._generation:
                        self.last_error = str(exc)
                        self.router._emit("audio_error", message=self.last_error)
            finally:
                with self._condition:
                    if self._active is state:
                        self._active = None
                    if generation == self._generation:
                        self._outstanding -= 1
                    self._condition.notify_all()
                self._queue.task_done()

    def is_speaking(self):
        with self._lock:
            return self._outstanding > 0

    def wait_until_done(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while self._outstanding:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return False
                self._condition.wait(remaining)
        self.check_errors()
        return True

    def check_errors(self):
        """Raise a real playback error so a failed paragraph cannot look read."""
        with self._lock:
            error = self.last_error
        if error:
            raise RuntimeError(error)

    def stop(self):
        with self._condition:
            if self._closed:
                return
            self._generation += 1
            self._paused = False
            if self._active:
                self._active.control("stop")
            self._outstanding = 0
            while True:
                try:
                    item = self._queue.get_nowait()
                except queue.Empty:
                    break
                self._queue.task_done()
            while True:
                try:
                    self._prefetch_queue.get_nowait()
                    self._prefetch_queue.task_done()
                except queue.Empty:
                    break
            self._condition.notify_all()

    def pause(self):
        with self._lock:
            self._paused = True
            if self._active:
                self._active.control("pause")

    def resume(self):
        with self._lock:
            self._paused = False
            if self._active:
                self._active.control("resume")

    def is_paused(self):
        with self._lock:
            return self._paused

    def set_speed(self, rate):
        with self._lock:
            self._speed = max(0.5, min(3.0, float(rate)))
            if self._active:
                self._active.speed = self._speed

    def get_speed(self):
        with self._lock:
            return self._speed

    def close(self):
        self.stop()
        with self._lock:
            if not self._closed:
                self._closed = True
                self._queue.put_nowait(None)
                self._prefetch_queue.put_nowait(None)


class PiAudioClient:
    """Reconnectable Pi playback endpoint, independent of video streaming."""
    def __init__(self, host, port=AUDIO_PORT, player=None, retry_delay=3.0,
                 status_callback=print):
        self.host, self.port = host, port
        self.player = player or PCMPlayer(
            device=os.environ.get("FYDP_PI_AUDIO_DEVICE") or None,
            allow_aplay=True)
        self.retry_delay = retry_delay
        self.status_callback = status_callback
        self._closed = threading.Event()
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._socket = None
        self._queue = queue.Queue(maxsize=2)
        self._states = {}
        self._started = False

    def start(self):
        if self._started:
            return
        self._started = True
        threading.Thread(target=self._worker, daemon=True,
                         name="pi-audio-player").start()
        threading.Thread(target=self._connect_loop, daemon=True,
                         name="pi-audio-connection").start()

    def _status(self, message):
        if self.status_callback:
            self.status_callback("[AUDIO] " + message)

    def _connect_loop(self):
        while not self._closed.is_set():
            sock = None
            try:
                backend = self.player.available()
                if not backend:
                    raise RuntimeError(
                        "No usable Pi output; connect headphones/speaker and "
                        "select the ALSA device. Camera streaming continues.")
                sock = socket.create_connection((self.host, self.port), timeout=4)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                _send_packet(sock, self._send_lock, {
                    "type": "hello", "protocol": PROTOCOL_VERSION,
                    "audio_ready": True, "backend": backend})
                hello, pcm = _recv_packet(sock)
                if pcm or hello.get("type") != "hello":
                    raise ValueError("Invalid host audio handshake")
                sock.settimeout(8)
                with self._lock:
                    self._socket = sock
                self._status(f"Ready on {self.host}:{self.port} ({backend})")
                threading.Thread(
                    target=self._heartbeat, args=(sock,), daemon=True,
                    name="pi-audio-heartbeat").start()
                while not self._closed.is_set():
                    message, pcm = _recv_packet(sock)
                    self._handle(sock, message, pcm)
            except Exception as exc:
                if not self._closed.is_set():
                    self._status(f"{exc}; retrying in {self.retry_delay:g}s")
            finally:
                with self._lock:
                    if self._socket is sock:
                        self._socket = None
                    for _, state in self._states.values():
                        state.control("stop")
                if sock is not None:
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    sock.close()
            self._closed.wait(self.retry_delay)

    def _heartbeat(self, sock):
        while not self._closed.wait(2):
            with self._lock:
                if self._socket is not sock:
                    return
            try:
                _send_packet(sock, self._send_lock, {"type": "ping"})
            except OSError:
                return

    def _handle(self, sock, message, pcm):
        kind = message.get("type")
        if kind == "pong" and not pcm:
            return
        if kind == "control" and not pcm:
            with self._lock:
                item = self._states.get(message.get("id"))
            if item and item[0] is sock:
                action = message.get("action")
                if action not in CONTROL_ACTIONS and action != "speed":
                    raise ValueError("Unsupported playback control")
                item[1].control(action, message.get("speed"))
            return
        if kind != "play":
            raise ValueError("Unexpected host audio message")
        identifier = message.get("id")
        sample_rate, channels = message.get("sample_rate"), message.get("channels")
        if (not isinstance(identifier, str) or not 1 <= len(identifier) <= 64 or
                type(sample_rate) is not int or not 8000 <= sample_rate <= 96000 or
                type(channels) is not int or channels not in (1, 2) or
                not pcm or len(pcm) % (2 * channels) or
                len(pcm) / (sample_rate * channels * 2) > MAX_AUDIO_SECONDS):
            raise ValueError("Invalid bounded PCM playback request")
        state = PlaybackState(message.get("speed", 1.0))
        if message.get("paused"):
            state.paused.set()
        with self._lock:
            if identifier in self._states:
                raise ValueError("Duplicate audio request identifier")
            self._states[identifier] = (sock, state)
        try:
            self._queue.put_nowait((sock, identifier, pcm, sample_rate, channels, state))
        except queue.Full:
            with self._lock:
                self._states.pop(identifier, None)
            _send_packet(sock, self._send_lock, {
                "type": "done", "id": identifier, "status": "error",
                "error": "Pi audio queue is full"})

    def _worker(self):
        while not self._closed.is_set():
            try:
                item = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            sock, identifier, raw, sample_rate, channels, state = item
            status, error = "ok", None
            try:
                if not state.cancel.is_set():
                    _send_packet(sock, self._send_lock, {
                        "type": "started", "id": identifier})
                    pcm = np.frombuffer(raw, dtype="<i2").reshape(-1, channels)
                    self.player.play(pcm, sample_rate, state)
                if state.cancel.is_set():
                    status = "stopped"
            except Exception as exc:
                status, error = "error", str(exc)
            finally:
                with self._lock:
                    self._states.pop(identifier, None)
                try:
                    _send_packet(sock, self._send_lock, {
                        "type": "done", "id": identifier,
                        "status": status, "error": error})
                except OSError:
                    pass
                self._queue.task_done()

    def send_control(self, action):
        """Optional future GPIO buttons can use this canonical event API."""
        if action not in CONTROL_ACTIONS:
            raise ValueError("Unsupported hardware audio control")
        with self._lock:
            sock = self._socket
        if sock is None:
            return False
        try:
            _send_packet(sock, self._send_lock, {
                "type": "control", "action": action})
            return True
        except OSError:
            return False

    def close(self):
        self._closed.set()
        with self._lock:
            sock = self._socket
            for _, state in self._states.values():
                state.control("stop")
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
