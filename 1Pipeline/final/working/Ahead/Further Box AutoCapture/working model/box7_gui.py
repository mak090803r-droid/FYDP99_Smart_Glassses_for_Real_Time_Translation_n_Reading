"""Small optional voice panel added to the existing Tk application."""
import threading
import time
import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText
from box7_commands import CaptureHold, parse_command
from box7_runtime import PlaybackControls
from box7_voice import VoiceService


def make_app_class(host):
    class Box7App(host.Box5App):
        def __init__(self, root, test_mode=False):
            self._voice_phase = "initializing"
            self._g_down = False
            self._g_release = None
            self._hold_timer = None
            self.voice_service = None
            super().__init__(root, test_mode)
            self.root.title("FYDP Smart Glasses — Box7")
            self.voice_service = VoiceService(host.PIPELINE_DIR,
                lambda: (self._voice_phase, tuple(self.tts_modules)), self.publish_event)
            PlaybackControls.voice = self.voice_service
            self._capture_hold = CaptureHold(self._hold_action)
            self.capture_button.bind("<ButtonPress-1>", self._capture_press)
            self.capture_button.bind("<ButtonRelease-1>", self._capture_release)
            self.root.bind_all("<KeyRelease-g>", self._release_g, add="+")
            self.root.bind_all("<KeyRelease-G>", self._release_g, add="+")
            self.root.bind("<FocusOut>", self._focus_out, add="+")

        def _build_controls(self, parent):
            super()._build_controls(parent)
            self.voice_var = tk.BooleanVar(value=False)
            self.voice_source = tk.StringVar(value="Pi earbuds")
            self.voice_device = tk.StringVar(value="default")
            self.voice_status = tk.StringVar(value="OFF — no voice model running")
            frame = ttk.LabelFrame(parent, text="VOICE INPUT (optional)")
            frame.pack(fill=tk.X, padx=5, pady=6)
            ttk.Checkbutton(frame, text="Enable voice input", variable=self.voice_var,
                            command=self._voice_changed).pack(anchor="w", padx=4)
            ttk.Label(frame, text="Microphone location").pack(anchor="w", padx=4)
            self.voice_source_combo = ttk.Combobox(frame, textvariable=self.voice_source,
                values=("Pi earbuds", "PC microphone"), state="readonly", width=21)
            self.voice_source_combo.pack(fill=tk.X, padx=4, pady=2)
            self.voice_source_combo.bind("<<ComboboxSelected>>", self._voice_changed)
            ttk.Label(frame, text="Input device/source (or default)").pack(anchor="w", padx=4)
            entry = ttk.Entry(frame, textvariable=self.voice_device, width=22)
            entry.pack(fill=tk.X, padx=4)
            entry.bind("<Return>", self._voice_changed)
            ttk.Label(frame, text="Hold G or Capture (0.7 s).\nRelease to submit. Speak English.",
                      wraplength=210).pack(anchor="w", padx=4, pady=3)
            ttk.Label(frame, textvariable=self.voice_status, wraplength=205).pack(anchor="w", padx=4, pady=3)

        def _build_interface(self):
            super()._build_interface()
            self.voice_tab = ttk.Frame(self.notebook)
            self.notebook.add(self.voice_tab, text="Voice")
            ttk.Label(self.voice_tab, text="Commands and spoken questions", font=("Tahoma", 10, "bold")).pack(anchor="w", padx=8, pady=6)
            ttk.Label(self.voice_tab, text=(
                "Hold G or hold Capture for 0.7 seconds; wait for LISTENING before speaking.\n"
                "Repeat the paragraph • Read paragraph 2 • Read from paragraph 2 • Read all headings\n"
                "What is the heading? • How many paragraphs? • Where am I? • Continue reading\n"
                "Skip paragraph 2 continues after paragraph 2. Numbered repeat reads that paragraph only.\n"
                "Questions are transcribed here. Document retrieval/answers are the next stage."),
                justify=tk.LEFT).pack(anchor="w", padx=8, pady=4)
            row = ttk.Frame(self.voice_tab)
            row.pack(fill=tk.X, padx=8, pady=4)
            self.voice_text = tk.StringVar()
            ttk.Entry(row, textvariable=self.voice_text).pack(side=tk.LEFT, fill=tk.X, expand=True)
            ttk.Button(row, text="Try typed command", command=self._typed_command).pack(side=tk.LEFT, padx=4)
            ttk.Button(row, text="List PC microphones", command=self._list_inputs).pack(side=tk.LEFT, padx=4)
            self.voice_log = ScrolledText(self.voice_tab, height=12, font=("Consolas", 10), state=tk.DISABLED)
            self.voice_log.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)

        def _voice_changed(self, event=None):
            if self.voice_service is None:
                return
            if self.voice_service.busy:
                self.voice_service.cancel()
            try:
                self.voice_service.configure(bool(self.voice_var.get()), self.voice_source.get(), self.voice_device.get())
            except Exception as exc:
                self.voice_var.set(False)
                self.voice_status.set(str(exc))
            self._g_down = False
            self._capture_hold.cancel()

        def _hold_action(self, action):
            if action == "capture":
                self.request_capture()
            elif action == "ptt_start":
                self.voice_service.start_input()
            elif action == "ptt_end":
                self.voice_service.end_input()
            else:
                self.voice_service.cancel()

        def _capture_press(self, event=None):
            if self.capture_button.instate(["disabled"]):
                return "break"
            if not self.voice_service.enabled:
                return None  # Keep ttk's original click/release behavior.
            self.capture_button.state(["pressed"])
            self._capture_hold.press(time.monotonic(), self.voice_service.enabled)
            self._hold_tick()
            return "break"

        def _hold_tick(self):
            self._capture_hold.tick(time.monotonic())
            if self._capture_hold.down:
                self._hold_timer = self.root.after(30, self._hold_tick)

        def _capture_release(self, event=None):
            if not self._capture_hold.down:
                return None
            self.capture_button.state(["!pressed"])
            self._capture_hold.release(time.monotonic())
            if self._hold_timer:
                self.root.after_cancel(self._hold_timer)
                self._hold_timer = None
            return "break"

        def _key_pressed(self, event):
            if event.widget.winfo_class() in ("Text", "Entry", "TEntry", "TCombobox", "Spinbox"):
                return
            if (event.char or "").lower() == "g":
                if self._g_release is not None:
                    self.root.after_cancel(self._g_release)
                    self._g_release = None
                if not self._g_down:
                    self._g_down = True
                    self.voice_service.start_input()
                return "break"
            return super()._key_pressed(event)

        def _release_g(self, event=None):
            if self._g_down:
                # Suppress synthetic release/press pairs from Linux autorepeat.
                self._g_release = self.root.after(75, self._finish_g)

        def _finish_g(self):
            self._g_release = None
            if self._g_down:
                self._g_down = False
                self.voice_service.end_input()

        def _focus_out(self, event=None):
            def check():
                if self.root.focus_displayof() is None:
                    self._finish_g()
                    if self._capture_hold.down:
                        self._capture_hold.cancel()
                        self.capture_button.state(["!pressed"])
            self.root.after(100, check)

        def request_capture(self):
            if self.voice_service and self.voice_service.busy:
                self.voice_service.cancel(restore=False)
            return super().request_capture()

        def request_stop_audio(self):
            if self.voice_service:
                self.voice_service.cancel(restore=False)
            return super().request_stop_audio()

        def publish_event(self, event_type, **payload):
            # Worker threads update plain data, never Tk variables.
            if event_type == "stage":
                self._voice_phase = payload.get("stage", "initializing")
                if self._voice_phase in ("capture", "ocr", "translation") and self.voice_service and self.voice_service.busy:
                    self.voice_service.cancel(restore=False)
            elif event_type == "session_ended":
                self._voice_phase = "stopped"
            elif event_type == "status" and payload.get("pi") == "DISCONNECTED":
                self._voice_phase = "disconnected"
                if self.voice_service and self.voice_service.busy:
                    self.voice_service.cancel(restore=False)
            if event_type in ("voice", "voice_question"):
                host._safe_debug_call(host._ACTIVE_DEBUG_RECORDER, "record_event", event_type, **payload)
            super().publish_event(event_type, **payload)

        def _handle_event(self, event_type, payload):
            if event_type in ("voice", "voice_question"):
                text = payload.get("message", "Question: " + payload.get("question", ""))
                self.voice_status.set(text if len(text) <= 150 else text[:147] + "...")
                self.voice_log.configure(state=tk.NORMAL)
                self.voice_log.insert(tk.END, time.strftime("%H:%M:%S ") + text + "\n")
                if int(self.voice_log.index("end-1c").split(".")[0]) > 300:
                    self.voice_log.delete("1.0", "100.0")
                self.voice_log.see(tk.END)
                self.voice_log.configure(state=tk.DISABLED)
                return
            super()._handle_event(event_type, payload)

        def _typed_command(self):
            if not self.voice_service.enabled:
                self.voice_status.set("Enable Voice Input first")
                return
            token = self.voice_service.begin()
            if token is not None:
                command = parse_command(self.voice_text.get())
                self.voice_service._commands.put_nowait(command)
                self.publish_event("voice", message="Typed: " + command.text, action=command.action)

        def _list_inputs(self):
            def query():
                try:
                    import sounddevice as sd
                    inputs = [f"{i}: {d['name']}" for i, d in enumerate(sd.query_devices()) if d['max_input_channels'] > 0]
                    self.publish_event("voice", message="PC inputs (enter index above):\n" + ("\n".join(inputs) or "No microphone available"))
                except Exception as exc:
                    self.publish_event("voice", message=str(exc))
            threading.Thread(target=query, daemon=True).start()

        def start_system(self):
            self._voice_phase = "initializing"
            super().start_system()
            if self.test_mode:
                self._voice_phase = "camera"

        def request_close(self):
            self._finish_g()
            self._capture_hold.cancel()
            self.voice_service.close()
            PlaybackControls.voice = None
            super().request_close()

    return Box7App
