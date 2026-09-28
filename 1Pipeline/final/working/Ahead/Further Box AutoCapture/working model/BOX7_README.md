# Box7 — optional push-to-talk, Stage 1

Run the host with the existing project Python:

```powershell
& 'C:\Users\ali\Desktop\FYDP\fydp\Scripts\python.exe' 'C:\Users\ali\Desktop\FYDP\New\Project\1Pipeline\final\working\Ahead\Further Box AutoCapture\working model\pipeline_cli_box7.py'
```

Box6, Pi3 and all Box6 helper modules remain the rollback baseline. Box7 is a
separate host copy with small integration points and optional `box7_*.py` modules.
Normal CLI operation is retained; the voice feature is configured through the GUI.

## Operation

1. Initialize/connect as before. Select the source language, output language,
   Book Mode, playback speed and Audio(Pi) as usual.
2. Check **Enable voice input** in the left panel. Default is OFF. Choose
   **Pi earbuds** or **PC microphone**, and a device/source if needed.
3. In the PC GUI, hold **G** to record and release to submit. G is ignored while
   typing in a text field. Alternatively hold **Start Capture** for at least
   0.7 seconds. Wait for LISTENING before speaking.
4. On Pi4, hold the existing **GPIO17 Capture button** for 0.7 seconds. Release
   it after speaking. A short press captures on release when voice is enabled;
   when disabled, Capture retains its press behavior. GPIO27 still toggles pause.
5. **G in the Pi terminal** toggles recording: press once to start and once to
   submit. Ordinary terminal input has no key-release events. GUI G is hold/release.

Recording is limited to 30 seconds. The recognizer is English `small.en`;
English/Urdu document TTS still uses the selected existing Piper voice.
Questions are transcribed and shown on the Voice tab, with a spoken explanation
that source retrieval is Stage 2. There is no embedding model, table model or
generated document answer in this stage.

## Commands

| Say | Behavior |
|---|---|
| Repeat the paragraph | Read the current region once |
| Repeat/read paragraph 1, 2, 3… | Read that numbered body paragraph once |
| Read from paragraph 2 | Continue reading from paragraph 2 onward |
| Go to paragraph 2 | Same as read from paragraph 2 |
| Skip paragraph 1 | Continue after paragraph 1 |
| Next paragraph / previous paragraph | Move one document region forward/back |
| What's the heading? | Read the nearest preceding detected heading/title |
| Repeat the headings / read all headings | Read detected title and section headings |
| What is the title? | Read the detected page title |
| How many paragraphs? | Speak paragraph/heading/title counts |
| Where am I? | Speak the saved reading position |
| Continue reading | Resume paused audio, or return to the saved paragraph |
| Read the whole page | Start again from the first region |
| Pause reading / stop reading | Explicit pause / stop |
| Voice help | Speak the available commands |

Paragraph numbers refer to the existing GUI's body-paragraph labels; headings do
not consume body-paragraph numbers. Out-of-range and ambiguous requests are
rejected. Heading and numbered-paragraph previews preserve the normal reading
bookmark. During active reading, paragraph/heading previews automatically return
to the interrupted paragraph and continue through the rest of the page. Nested
preview requests retain that original return point. Explicit Stop still stops;
read-from/go-to/skip commands deliberately change the continuation point.
Requests made after reading has already stopped or finished remain one-off
previews. After an informational answer, **Continue reading** restarts the saved
paragraph. Return is paragraph-level, not the exact interrupted word.
For an ordinary pause/resume command during a clip, existing PCM position is kept.

The Voice tab also provides a typed-command field for testing the command handler
without a microphone and a button to list PC microphone indices.

## QCY Bluetooth on the Pi

Pair the earbuds in Raspberry Pi OS and select their **Headset / HFP / HSP**
profile so both microphone and playback are exposed. Microphone mode can reduce
playback fidelity. Box7 does not switch profiles underneath an active TTS stream.
The OS needs a working Bluetooth audio backend (for example PipeWire/PulseAudio).

On the Pi, run:

```bash
python piweb_cli4.py --voice-devices
```

With `parec` available, source `default` selects the single exposed Bluetooth
microphone (or the identifiable QCY source when there are several). An explicit
Pulse source name overrides this. Monitor sources are never selected automatically.
For a different microphone, enter its explicit source name. If only `arecord` is
available, select an ALSA capture device exposed by the OS.

No native Pi microphone/profile test has been performed by the local validation.
The PC currently lists QCY ArcBuds audio endpoints, which does not prove that the
Pi is paired, has the headset profile enabled, or can record and play correctly.

## Pi files

Copy these six files together to the same folder on the Pi:

- `piweb_cli4.py`
- `box7_commands.py`
- `box7_pi_voice.py`
- `box7_voice_io.py`
- `box6_audio.py` (unchanged)
- `box7_pi_audio.py` (native PipeWire playback)

Then run `python piweb_cli4.py`. `FYDP_PC_IP` still sets the host IP if needed.
Camera remains TCP 9999, TTS remains TCP 10000, and voice input uses TCP 10001.
Allow TCP 10001 on the PC's private network if its firewall blocks the Pi.
These are local-network protocols, not services intended for Internet exposure.

The Pi uses its existing camera/NumPy/GPIO environment and `pw-cat`, `parec` or `arecord`
for input; no Whisper/NLLB model runs on the Pi. The new files have been created
locally, not deployed to or launched on your Pi automatically.

PipeWire systems use `pw-cat` natively for microphone capture and playback.
The `default` input follows the OS default microphone; select QCY as the default
source with the Pi audio controls. An explicit input is a PipeWire node name or
serial. Default output follows the OS-selected sink. `FYDP_PI_AUDIO_DEVICE`, if
set, retains the explicit legacy sounddevice/ALSA output selection instead.

## Optional voice environment and model

The new `.venv_box7_voice` is used only by the ASR subprocess. The existing `fydp`
environment has not been upgraded. The English Whisper model is stored at
`box7_models/faster-whisper-small.en` (approximately 486 MB). The one-time setup
uses `faster-whisper==1.2.1`. To recreate it explicitly on this Windows PC:

```powershell
& 'C:\Users\ali\Desktop\FYDP\fydp\Scripts\python.exe' -m venv --system-site-packages .venv_box7_voice
& '.\.venv_box7_voice\Scripts\python.exe' -m pip install 'faster-whisper==1.2.1'
& '.\.venv_box7_voice\Scripts\python.exe' '.\setup_box7_voice.py'
```

Only `setup_box7_voice.py` downloads model assets. Runtime ASR uses local paths
and offline flags and fails explicitly when assets are missing. `FYDP_VOICE_PYTHON`
and `FYDP_WHISPER_MODEL` can override those local paths.

Whisper runs on CPU, INT8, two threads, in a separate process. It starts on the
first submitted recording, remains idle for subsequent requests, and unloads
when voice is disabled or the session closes. First use may be slower due to
model/import loading. Requests during capture, OCR, translation or initialization
are rejected. A new capture cancels active ASR. There is no ASR inference, audio
recording or document indexing in the ordinary page-processing path.

## Validation

Run from this folder with the existing `fydp` Python:

```powershell
& 'C:\Users\ali\Desktop\FYDP\fydp\Scripts\python.exe' '.\box7_validation\run_validation.py'
& 'C:\Users\ali\Desktop\FYDP\fydp\Scripts\python.exe' '.\box7_validation\run_validation.py' --models
```

Reports and isolated generated outputs are under `box7_validation`. Unit checks
cover baseline file hashes, unchanged core algorithms, control behavior, hold
semantics, numbered regions, heading previews, cancellation and loopback Pi voice
transport. One old Box6 test names a deleted OCR log; the runner transparently
uses a surviving archived OCR result for that one test, without altering old logs.

Model checks compare saved captures and raw debug-session frames, run actual
OCR/NLLB/Piper with voice off and idle, replay recorded Pi control messages, and
test Whisper using synthetic Piper speech. Audio samples are generated into a
silent sink. Piper's random synthesis variation means PCM hashes can differ for
identical input; exact spoken requests, OCR/translation text, region identity and
valid PCM generation are checked separately. Physical QCY/GPIO/live-camera tests
and microphone accuracy with your voice remain hardware acceptance steps.
