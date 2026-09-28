"""
piweb_cli3.py (CLI / Keyboard + Two GPIO Buttons)
=================================================
Raspberry Pi camera streamer with independent PC-to-Pi PCM audio playback.
Copy box6_audio.py beside this file. Select Audio(Pi) in the Box6 GUI.
Audio uses TCP port 10000; camera messages keep the original port/protocol.
No Piper, NLLB, or additional model runs on the Pi.

Original keyboard controls plus:

    Capture button (GPIO 17) -> same action as S
    Pause button   (GPIO 27) -> same action as A (toggle TTS pause/resume)

Wire each normally-open push button between its GPIO pin and a GND pin.
Internal pull-up resistors are enabled, so no external resistor is required.

Controls:
    S or capture button -> send capture signal to PC
    A or pause button   -> toggle TTS pause/resume
    N / P / R           -> next / previous / repeat document region
    Q                   -> quit

Usage on Pi:
    python piweb_cli3.py

The original camera, network, FPS, resolution, and JPEG settings are unchanged.
"""

import cv2
import socket
import pickle
import struct
import time
import json
import threading
import sys
import os
import numpy as np

from box6_audio import PiAudioClient, AUDIO_PORT

try:
    import RPi.GPIO as GPIO
except ImportError:
    GPIO = None


# =============================================================================
# CONFIG -- original piweb_cli.py values are unchanged
# =============================================================================
PC_IP        = os.environ.get('FYDP_PC_IP', '192.168.137.1')
PORT         = 9999
RETRY_DELAY  = 3

# Camera
CAM_INDEX    = 0
CAM_WIDTH    = 1920
CAM_HEIGHT   = 1080
CAM_FPS      = 15
JPEG_QUALITY = 95

# Buttons (BCM numbering; each button connects its GPIO pin to GND)
CAPTURE_BUTTON_PIN = 17
PAUSE_BUTTON_PIN   = 27
BUTTON_BOUNCE_MS   = 300


# =============================================================================
# WIRE PROTOCOL -- must match the PC pipeline
# =============================================================================
MSG_JSON  = 0x01
MSG_FRAME = 0x02

_send_lock = threading.Lock()


def send_json_safe(sock, obj):
    """Thread-safe JSON send."""
    data = json.dumps(obj).encode("utf-8")
    with _send_lock:
        sock.sendall(struct.pack("!BI", MSG_JSON, len(data)) + data)


def send_frame_safe(sock, jpeg_buf):
    """Thread-safe frame send."""
    data = pickle.dumps(jpeg_buf)
    with _send_lock:
        sock.sendall(struct.pack("!BQ", MSG_FRAME, len(data)) + data)


# =============================================================================
# CAMERA (unchanged from piweb_cli.py)
# =============================================================================
class CameraStream:
    """Reads frames in a dedicated thread and returns the latest frame."""

    def __init__(self, index, width, height, fps):
        self.cap = cv2.VideoCapture(index, cv2.CAP_V4L2)
        if not self.cap.isOpened():
            self.cap = cv2.VideoCapture(index)
        if not self.cap.isOpened():
            raise RuntimeError(f"Cannot open camera at index {index}")

        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH,  width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.cap.set(cv2.CAP_PROP_FPS,           fps)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        f = self.cap.get(cv2.CAP_PROP_FPS)
        print(f"  Camera opened: {w}x{h} @ {f:.0f} fps")

        self._frame   = None
        self._lock    = threading.Lock()
        self._running = True
        self._thread  = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while self._running:
            ok, frame = self.cap.read()
            if ok:
                with self._lock:
                    self._frame = frame

    def read(self):
        with self._lock:
            return self._frame.copy() if self._frame is not None else None

    def release(self):
        self._running = False
        self._thread.join(timeout=2)
        self.cap.release()


# =============================================================================
# KEYBOARD LISTENER (unchanged, with A added to the main key handling)
# =============================================================================
_key_queue = None


def _keyboard_listener_windows():
    """Windows: use msvcrt for non-blocking key input."""
    import msvcrt
    while True:
        if msvcrt.kbhit():
            key = msvcrt.getch().decode("utf-8", errors="ignore").lower()
            _key_queue.put(key)
        time.sleep(0.02)


def _keyboard_listener_unix():
    """Unix/Linux: use select + termios for raw non-blocking input."""
    import termios
    import tty
    import select

    old_settings = termios.tcgetattr(sys.stdin)
    try:
        tty.setraw(sys.stdin.fileno())
        while True:
            if select.select([sys.stdin], [], [], 0.02)[0]:
                key = sys.stdin.read(1).lower()
                _key_queue.put(key)
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)


def start_keyboard_listener():
    """Start a background thread that reads keyboard input."""
    import queue as _q
    global _key_queue
    _key_queue = _q.Queue()

    if not sys.stdin.isatty():
        print("  Non-interactive terminal: GPIO controls remain available.")
        return _key_queue

    if sys.platform == "win32":
        target = _keyboard_listener_windows
    else:
        target = _keyboard_listener_unix

    t = threading.Thread(target=target, daemon=True)
    t.start()
    return _key_queue


# =============================================================================
# GPIO BUTTONS
# =============================================================================
_gpio_ready = False


def _capture_button_callback(_channel):
    """Put the same S action used by the keyboard into the shared queue."""
    if _key_queue is not None:
        _key_queue.put('s')


def _pause_button_callback(_channel):
    """Put the same A action used by the keyboard into the shared queue."""
    if _key_queue is not None:
        _key_queue.put('a')


def setup_gpio_buttons():
    """Configure two active-low buttons using the Pi's internal pull-ups."""
    global _gpio_ready

    if GPIO is None:
        print("  [WARN] RPi.GPIO unavailable; keyboard controls remain active.")
        return

    try:
        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(CAPTURE_BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.setup(PAUSE_BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.add_event_detect(
            CAPTURE_BUTTON_PIN,
            GPIO.FALLING,
            callback=_capture_button_callback,
            bouncetime=BUTTON_BOUNCE_MS)
        GPIO.add_event_detect(
            PAUSE_BUTTON_PIN,
            GPIO.FALLING,
            callback=_pause_button_callback,
            bouncetime=BUTTON_BOUNCE_MS)
        _gpio_ready = True
        print(
            f"  GPIO buttons active: capture=GPIO{CAPTURE_BUTTON_PIN}, "
            f"pause/resume=GPIO{PAUSE_BUTTON_PIN}\n")
    except Exception as exc:
        print(f"  [WARN] GPIO button setup failed: {exc}")
        try:
            GPIO.cleanup()
        except Exception:
            pass


def cleanup_gpio():
    """Release GPIO resources if setup completed successfully."""
    if GPIO is not None and _gpio_ready:
        GPIO.cleanup()


# =============================================================================
# MAIN
# =============================================================================
def main():
    print("=" * 58)
    print("  piweb_cli3.py -- FYDP Smart Glasses (Keyboard + GPIO + Pi Audio)")
    print("=" * 58)
    print("\n  Controls:")
    print("    S / GPIO17  -> Capture; during TTS it stops playback")
    print("    A / GPIO27  -> Toggle TTS pause/resume")
    print("    N / P / R   -> Next / previous / repeat document region")
    print("    Q           -> Quit\n")

    # This connection cannot hold up camera streaming or change its protocol.
    audio_client = PiAudioClient(PC_IP, port=AUDIO_PORT)
    audio_client.start()

    # Camera
    try:
        cam = CameraStream(CAM_INDEX, CAM_WIDTH, CAM_HEIGHT, CAM_FPS)
    except Exception:
        audio_client.close()
        raise
    print("  Letting auto-exposure stabilize ...")
    time.sleep(4)
    for _ in range(10):
        cam.read()
        time.sleep(0.02)
    print("  Camera ready!\n")

    # Keyboard and GPIO buttons share one action queue. This guarantees that
    # button S/A behavior follows exactly the same code path as keyboard S/A.
    key_queue = start_keyboard_listener()
    print("  Keyboard listener active.")
    setup_gpio_buttons()

    # JPEG encode params
    _enc = [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]

    # Main loop: connect, stream, reconnect
    try:
        while True:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)

            try:
                print(f"Connecting to PC at {PC_IP}:{PORT} ...")
                sock.connect((PC_IP, PORT))
                sock.settimeout(5)  # Reconnect if the host stops reading video.
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1 << 20)
                print("Connected! Streaming ...\n")

            except (ConnectionRefusedError, OSError, socket.timeout):
                print(f"PC not ready. Retrying in {RETRY_DELAY}s ...")
                sock.close()
                time.sleep(RETRY_DELAY)
                continue

            # Stream + input loop
            n_frames = 0
            t0 = time.monotonic()

            try:
                while True:
                    # GPIO callbacks place S/A in the same queue as keyboard.
                    try:
                        while True:
                            key = key_queue.get_nowait()
                            if key == 'q':
                                print("\nQ pressed -- quitting.")
                                sock.close()
                                cam.release()
                                sys.exit(0)
                            elif key == 's':
                                print("  Capture signal sent to PC!")
                                try:
                                    send_json_safe(sock, {"cmd": "capture_from_pi"})
                                except Exception:
                                    pass
                            elif key in ('n', 'p', 'r'):
                                action = {'n': 'next', 'p': 'previous', 'r': 'repeat'}[key]
                                send_json_safe(sock, {
                                    'cmd': 'tts_control', 'action': action})
                            elif key == 'a':
                                print("  TTS pause/resume signal sent to PC!")
                                try:
                                    # This is the existing Pi TTS-control
                                    # protocol already used by piweb.py.
                                    send_json_safe(
                                        sock,
                                        {
                                            "resp": "tts_control",
                                            "action": "pause",
                                            "cmd": "tts_pause_toggle_from_pi"
                                        })
                                except Exception:
                                    pass
                    except Exception:
                        pass  # queue empty

                    # Read & send frame
                    frame = cam.read()
                    if frame is None:
                        time.sleep(0.005)
                        continue

                    ok, buf = cv2.imencode('.jpg', frame, _enc)
                    if not ok:
                        continue

                    send_frame_safe(sock, buf)

                    n_frames += 1
                    if n_frames % 30 == 0:
                        fps = n_frames / (time.monotonic() - t0)
                        kb = len(pickle.dumps(buf)) / 1024
                        print(
                            f"  {fps:.1f} fps | frame #{n_frames} | "
                            f"{kb:.0f} KB/frame")

            except Exception as exc:
                dt = time.monotonic() - t0
                print(
                    f"\nDisconnected after {n_frames} frames "
                    f"({dt:.1f}s): {exc}")
                print(f"   Reconnecting in {RETRY_DELAY}s ...\n")
            finally:
                sock.close()

            time.sleep(RETRY_DELAY)

    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        cam.release()
        audio_client.close()
        cleanup_gpio()
        print("Cleaned up. Goodbye.")


if __name__ == "__main__":
    main()


