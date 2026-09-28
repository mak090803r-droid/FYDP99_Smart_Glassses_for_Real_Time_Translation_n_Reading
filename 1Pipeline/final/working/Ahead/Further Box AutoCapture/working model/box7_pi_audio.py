"""Native PipeWire output for Pi4; preserve the shared Box6 player."""
import os
import shutil
import subprocess
import tempfile
import threading
import numpy as np
from box6_audio import PCMPlayer


class PiPCMPlayer(PCMPlayer):
    def _native(self):
        return os.name != "nt" and shutil.which("pw-cat") and not self.device

    def available(self):
        return "pipewire" if self._native() else super().available()

    def play(self, pcm, sample_rate, state):
        if not self._native():
            return super().play(pcm, sample_rate, state)
        if not len(pcm) or state.cancel.is_set():
            return
        done = threading.Event()
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(["pw-cat", "--playback", "--raw",
                "--rate=48000", "--channels=" + str(pcm.shape[1]),
                "--format=s16", "--latency=40ms", "-"],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errors, bufsize=0)
            def watch():
                while not done.wait(0.02):
                    if state.cancel.is_set():
                        if process.poll() is None:
                            process.terminate()
                        return
            threading.Thread(target=watch, daemon=True).start()
            try:
                position = 0.0
                while position < len(pcm) and not state.cancel.is_set():
                    if state.paused.is_set():
                        state.cancel.wait(0.02)
                        continue
                    step = sample_rate * state.speed / 48000
                    indices = position + np.arange(960) * step
                    indices = indices[indices < len(pcm)]
                    left = indices.astype(np.int64)
                    right = np.minimum(left + 1, len(pcm) - 1)
                    fraction = (indices - left)[:, None]
                    output = np.rint(pcm[left] * (1-fraction) + pcm[right] * fraction).astype('<i2').tobytes()
                    remaining = memoryview(output)
                    while remaining and not state.cancel.is_set():
                        count = process.stdin.write(remaining)
                        if not count:
                            raise BrokenPipeError("PipeWire output closed")
                        remaining = remaining[count:]
                    position += len(indices) * step
                process.stdin.close()
                code = process.wait(timeout=5)
                if code and not state.cancel.is_set():
                    raise OSError("PipeWire playback exited with code " + str(code))
            except (OSError, subprocess.TimeoutExpired) as exc:
                if not state.cancel.is_set():
                    errors.seek(0)
                    detail = errors.read().decode(errors="replace")[-1500:]
                    raise RuntimeError("Pi PipeWire playback failed: " + (detail.strip() or str(exc))) from exc
            finally:
                done.set()
                try:
                    process.stdin.close()
                except OSError:
                    pass
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill(); process.wait(timeout=2)
