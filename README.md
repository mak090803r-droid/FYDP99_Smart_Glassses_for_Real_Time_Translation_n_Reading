# FYDP Smart Glasses — Box10 setup and operation

This is a handoff guide for the **current PC entry point, `pipeline_cli_box10.py`**, and its Raspberry Pi 4B camera/button client, **`piweb_cli4.py`**. The PC does OCR, translation, speech synthesis, optional speech recognition, table analysis and source-linked questions. The Pi streams the webcam, sends button commands, and optionally plays PC-generated audio and records its headset microphone. Do not run an older `pipeline_cli_box*.py` or `piweb_cli*.py` by accident.

This guide was traced from the Box10/Pi4 source and the working Windows installation. It is **not** a claim that a new PC, GPU, Pi OS image or Bluetooth headset has been physically tested. Model weights are large; a ZIP containing just the `.py` files is not a complete offline installer. Start with the base OCR/English voice path, then enable optional features one at a time.

## 1. What to transfer

Copy the whole `Project` tree to a short Windows path such as `C:\SGPP\Project`. Keep these relationships intact:

```text
C:\SGPP\Project\
  pics\                             # required Project-root marker; captured images go in pics\captured
  nllb-200-1.3B-ct2\                 # model.bin, config.json, shared_vocabulary.json
  1Pipeline\
    piper_models\                    # en_US-lessac-medium and optional ur_PK-fasih-medium .onnx + .onnx.json
    final\
      pipertts.py
      working\Ahead\Further Box AutoCapture\working model\
        pipeline_cli_box10.py       # RUN THIS ON WINDOWS
        box10_gui.py                # plus all neighboring box6_*, box7_*, box8_*, box9_* helper .py files
        pipeline_cli_box9.py       # referenced by the optional table worker
        piweb_cli4.py              # copy to Pi; do NOT run on Windows
        setup_box9_models.py
        setup_box9_voice.py
        box8_models\table_detection\, table_structure\
        box9_models\qa\, faster-whisper-medium.en\
```

The working model folder contains other historical files, logs and tests; **copy it intact initially** so helper imports are not missed. Old virtual environments are machine-specific: recreate them on the destination rather than expecting a copied `fydp` or `.venv_box7_voice` to work. `paragraph_test_outputs` and `pics\captured` are generated output, not model assets.

The Paddle OCR weights are **outside** `Project` by default. Copy these three complete directories from the original PC's `%USERPROFILE%\.paddlex\official_models\` into the *new* PC's `%USERPROFILE%\.paddlex\official_models\`, or place them somewhere else and set `FYDP_PADDLE_MODEL_ROOT` to their **parent** directory:

| Directory | Required by code | When used |
|---|---|---|
| `PP-OCRv6_medium_det_onnx` | `inference.onnx`, `inference.yml` | All page capture/OCR |
| `PP-OCRv6_medium_rec_onnx` | `inference.onnx`, `inference.yml` | All page OCR, table-cell retry |
| `UVDoc` | `inference.pdiparams`, `inference.json`, `inference.yml` | Book Mode only |

Copy the complete model directories, not only the named files. Box10 checks local files and sets Hugging Face/Transformers offline mode at startup; it will **not** silently download missing OCR/NLLB/table/Whisper assets.

## 2. Fresh Windows PC

Use 64-bit Windows, Python **3.11** with Tkinter, enough disk space for the models, and preferably an NVIDIA CUDA GPU. The working PC used Python 3.11.1 and an RTX A4000; CPU fallback exists for some stages but is slower, and GPU/native DLL compatibility is **not** guaranteed by the Python package list alone. Install an NVIDIA driver and the Microsoft Visual C++ runtime before debugging GPU DLL errors. Use a short project path because model download/cache paths can otherwise exceed Windows path limits.

In PowerShell, after copying `Project`:

```powershell
$Project = 'C:\SGPP\Project'
$Work = Join-Path $Project '1Pipeline\final\working\Ahead\Further Box AutoCapture\working model'
py -3.11 -m venv C:\SGPP\fydp
$Py = 'C:\SGPP\fydp\Scripts\python.exe'
& $Py -m pip install --upgrade pip
& $Py -m pip install torch==2.6.0 torchvision==0.21.0 --index-url https://download.pytorch.org/whl/cu124
& $Py -m pip install -r 'C:\SGPP\!README\requirements-windows.txt'
& $Py -m pip install --no-deps piper-tts==1.4.2
& $Py -m pip check
& $Py -c "import torch, cv2, paddleocr, ctranslate2, transformers, piper, sounddevice, psutil; print('CUDA:', torch.cuda.is_available())"
```

Put this `!README` folder at `C:\SGPP\!README` to use the command verbatim, or adjust the `-r` path to where you saved `requirements-windows.txt`. The pinned packages were observed locally; they are a **direct-dependency snapshot, not a full tested clean-install lockfile**. PyTorch's published CUDA 12.4 wheel command is [here](https://docs.pytorch.org/get-started/previous-versions/); select a compatible wheel/driver for a different GPU. If a package resolver reports a conflict, investigate it; do not randomly upgrade the whole environment. Piper is installed with `--no-deps` here because its declared CPU `onnxruntime` dependency would otherwise overlap the selected `onnxruntime-gpu` wheel; its other direct dependency, `pathvalidate`, is in the requirements file. On the original PC, `pip check` reports that `piper-tts` and two unrelated packages request the distribution named `onnxruntime` even though `onnxruntime-gpu` is installed and Box10 imports correctly. This **metadata warning is known**, not proof of a runtime failure; do not blindly install both CPU and GPU ONNX wheels over each other. Box10 chooses CUDA when PyTorch reports it available; ONNX/Paddle/CTranslate2 also need their own compatible native CUDA libraries. `torch.cuda.is_available() == True` alone does not prove every model is on GPU.

For a PC without NVIDIA, use PyTorch's [CPU wheel instructions](https://docs.pytorch.org/get-started/previous-versions/) and expect slower operation; the supplied ONNX GPU package may need replacing with CPU ONNX Runtime. That is an adaptation, **not** the same hardware configuration as the working PC.

### Model checks and one-time provisioning

Run these checks from PowerShell. `Test-Path` should print `True` for each required asset before initialization:

```powershell
Test-Path "$Project\pics"
Test-Path "$Project\1Pipeline\final\pipertts.py"
Test-Path "$Project\nllb-200-1.3B-ct2\model.bin"
Test-Path "$Project\1Pipeline\piper_models\en_US-lessac-medium.onnx"
Test-Path "$env:USERPROFILE\.paddlex\official_models\PP-OCRv6_medium_det_onnx\inference.onnx"
Test-Path "$env:USERPROFILE\.paddlex\official_models\PP-OCRv6_medium_rec_onnx\inference.onnx"
```

If Paddle models are stored elsewhere, set `$env:FYDP_PADDLE_MODEL_ROOT='C:\SGPP\paddle_models'` **in the same PowerShell session used to launch Box10**. This folder must contain the three model directories listed above. If the English Piper files were not copied, the one-time downloader in `pipertts.py` can fetch them while online:

```powershell
Set-Location "$Project\1Pipeline\final"
& $Py -c "import pipertts; pipertts.ensure_model()"
```

For French, Chinese, Spanish or English-to-Urdu translation, the converted NLLB model must be at `$Project\nllb-200-1.3B-ct2`, and the tokenizer for `facebook/nllb-200-distilled-1.3B` must exist in that Windows user's Hugging Face cache. If it is not already cached, provision it **once while online, in the same Windows account**:

```powershell
& $Py -c "from transformers import AutoTokenizer; AutoTokenizer.from_pretrained('facebook/nllb-200-distilled-1.3B')"
```

The converted model and tokenizer are different assets; one does not replace the other. If distributing fully offline, also transfer the Hugging Face cache for that tokenizer into the new user's matching cache location (or provision it online first). English source **to English output** bypasses NLLB.

Optional Urdu output needs both `ur_PK-fasih-medium.onnx` and `.onnx.json` in `1Pipeline\piper_models`. Box10 validates them and falls back to English if unusable. Book Mode additionally needs UVDoc; flat-page OCR works with Book Mode off.

Optional source Q&A/table checkpoints and optional push-to-talk transcription can be copied from the tree above. If absent, while online run the existing provisioning scripts:

```powershell
Set-Location $Work
& $Py .\setup_box9_models.py
& $Py -m venv --system-site-packages .venv_box7_voice
& .\.venv_box7_voice\Scripts\python.exe -m pip install faster-whisper==1.2.1
& .\.venv_box7_voice\Scripts\python.exe .\setup_box9_voice.py
```

`setup_box9_models.py` obtains Microsoft Table Transformer detection/structure and `deepset/deberta-v3-base-squad2`; `setup_box9_voice.py` obtains `Systran/faster-whisper-medium.en`. Even if the Whisper **weights** were copied, create `.venv_box7_voice` because `box9_voice.py` requires its Python executable by default. The optional table worker also imports `timm`; the supplied `.box8_deps` directory currently contains it. If that directory is not shipped, install `timm==1.0.22` into the main environment. Do not use the older Box7/Box8 setup scripts expecting them to provision the current QA/ASR checkpoints.

## 3. Raspberry Pi 4B and hardware

Use Raspberry Pi OS **64-bit**, a USB UVC webcam (the project's Logitech C525), two normally-open momentary buttons, and a speaker/earbuds. Attach the webcam to the Pi and verify that the intended camera is OpenCV camera index **0**. `piweb_cli4.py` requests **1920×1080 at 15 fps**, JPEG quality **95**; actual delivered resolution/FPS depends on camera and USB link.

On the Pi, install its system-side Python and audio tools. Use the distro's OpenCV/NumPy/GPIO packages to avoid compiling large wheels on a Pi:

```bash
sudo apt update
sudo apt install -y python3 python3-opencv python3-numpy python3-rpi.gpio \
  pipewire pipewire-pulse pulseaudio-utils alsa-utils v4l-utils
python3 -c 'import cv2, numpy, RPi.GPIO; print(cv2.__version__)'
v4l2-ctl --list-devices
```

If your Pi OS release uses different package names, install its equivalent packages and make the import check pass; this exact apt transaction has **not** been verified on a fresh Pi image. No PaddleOCR, NLLB, Piper, Whisper or table model is installed on the Pi.

Copy **these six files from the Box10 working-model folder** together to `/home/<pi-user>/comms/` (or any one Pi directory): `piweb_cli4.py`, `box6_audio.py`, `box7_pi_audio.py`, `box7_pi_voice.py`, `box7_voice_io.py`, `box7_commands.py`. For example, from Windows PowerShell with SSH already enabled and `scp` available:

```powershell
scp "$Work\piweb_cli4.py" "$Work\box6_audio.py" "$Work\box7_pi_audio.py" "$Work\box7_pi_voice.py" "$Work\box7_voice_io.py" "$Work\box7_commands.py" <pi-user>@<pi-ip>:/home/<pi-user>/comms/
```

Replace `<pi-user>` and `<pi-ip>`; create `comms` on the Pi first (`mkdir -p ~/comms`). Do **not** put only `piweb_cli4.py` there: missing `box6_audio.py` causes `ModuleNotFoundError` immediately. Do not put passwords in the command or README. On the Pi, check `python3 -c 'import cv2, numpy, RPi.GPIO'` and `ls ~/comms` before running.

### Two GPIO buttons

The code uses **BCM GPIO numbering**, not physical pin numbers:

| Action | BCM GPIO | Pi 40-pin header | Wire the other switch terminal to |
|---|---:|---:|---|
| Capture / push-to-talk | GPIO17 | physical pin **11** | GND, e.g. physical pin **6** |
| Pause/resume | GPIO27 | physical pin **13** | GND, e.g. physical pin **14** |

Both inputs use built-in pull-ups. Power the Pi off before wiring. **Never connect these GPIO inputs directly to 5 V.** With voice input disabled, the capture button acts on press; with voice enabled, a short release sends capture and a hold of at least **0.7 seconds** starts push-to-talk (release submits speech). The pause button always toggles playback pause/resume. If GPIO initialization fails, the Pi terminal keyboard remains usable.

### Network and Pi audio

The PC and Pi need IP connectivity on the **same trusted local network**. The Pi defaults to PC IP `192.168.137.1` (often the Windows Internet Connection Sharing/Hotspot address); do **not** assume every new PC uses that address. Run `ipconfig` on Windows, find the interface reachable from the Pi, and set the actual address on the Pi. The Pi connects **outbound** to the PC on TCP **9999** (camera/control), **10000** (optional PCM audio), and **10001** (optional voice input). Allow these inbound TCP ports on the PC's **Private** firewall profile only; do not expose them to the Internet. From the Pi, `ping <PC-IP>` is a first network check.

The Pi's `FYDP_PC_IP` variable can be set for one terminal session:

```bash
export FYDP_PC_IP=<actual-PC-IP>
```

For audio from the Pi, pair/select the QCY (or other) earbuds in the Pi OS audio/Bluetooth settings and make them the **default output sink**. Select **Audio(Pi)** in the Windows GUI. The Pi player prefers `pw-cat`/PipeWire output. For push-to-talk using the Bluetooth microphone, select a **Headset/HFP/HSP** profile exposing a mic, then use the Pi OS audio controls to select it as input. This often lowers playback quality while the microphone is active. The code does not change the Bluetooth profile for you. Check devices with:

```bash
cd ~/comms
python3 piweb_cli4.py --voice-devices
pactl list short sinks
pactl list short sources
```

In the GUI's Voice Input panel choose **Pi earbuds**, enable voice, and leave the source as `default` only if the correct mic is the Pi's default. Otherwise enter its PipeWire/Pulse source name. PC microphone is a separate GUI option and does not require Pi Bluetooth input. `FYDP_PI_AUDIO_DEVICE` is an optional advanced Pi output-device override; leave it unset for ordinary PipeWire playback. Test camera, playback and microphone separately before combining them.

## 4. Actual startup order

1. Power the Pi, connect webcam/buttons/earbuds, and connect Pi and PC to the same local network. Pair/select audio devices in Pi OS if needed.
2. On Windows, open PowerShell and launch the main GUI (default; `--cli` is the older console mode):

   ```powershell
   $Project = 'C:\SGPP\Project'
   $Work = Join-Path $Project '1Pipeline\final\working\Ahead\Further Box AutoCapture\working model'
   & 'C:\SGPP\fydp\Scripts\python.exe' "$Work\pipeline_cli_box10.py"
   ```

3. Before clicking Initialize, choose **Printed document language** (French, Chinese, Spanish, English), **Translation/audio output** (English or Urdu), playback speed, and **Book mode** only for curved pages. Select **Audio(Pi)** if you want sound in the Pi earbuds. Normal PC audio is the default. Voice, tables and source-linked Q&A are independently **OFF** by default.
4. Click **Initialize / Connect**. The host checks native OCR dependencies, starts the audio router, loads English Piper, optional Urdu Piper, OCR, optional UVDoc, NLLB if translation is needed, and the spell checker. Then it listens on TCP 9999 and shows **WAITING FOR PI**. No capture starts yet.
5. On the Pi, from a local terminal or SSH session:

   ```bash
   cd ~/comms
   export FYDP_PC_IP=<actual-PC-IP>
   python3 piweb_cli4.py
   ```

   The Pi starts audio/voice connections, opens the webcam, stabilizes exposure, sets up keyboard/GPIO controls, then connects/streams to the PC. It retries the camera connection every 3 seconds if the PC is not ready. Once connected, the GUI should show **PI LINK: CONNECTED** and a live feed.
6. Put the printed page fully in view. Press **Start Capture (S)** or briefly press GPIO17. The PC checks framing/stability and gives positioning prompts. **Press Capture/S a second time while auto-capture is running to force a capture** if you are satisfied with the frame; an inadequate forced frame may still be rejected. After capture, it shows the captured/OCR regions, processes OCR, translates the full page when needed, and reads the regions sequentially while showing which one is active. When it finishes, another capture can start the next page.

The GUI has **Live**, **Text Output**, **Debug**, **Results**, **Voice**, and **Page tools** tabs. **Show OCR** opens the text output; **Open Debug** exposes runtime/debug information. The Results tab computes CER/WER only when you supply corresponding ground truth; it is not a live accuracy guarantee. **Demo Mode** reduces UI clutter, but does not change the reading pipeline. Debug sessions/images are written under `working model\paragraph_test_outputs` and captured photos under `Project\pics\captured`; watch disk usage if debug recording is enabled.

## 5. Controls while wearing the glasses

| Control | GUI / PC keyboard | Pi terminal / GPIO | Result |
|---|---|---|---|
| Capture | Start Capture / `S` | `S` / brief GPIO17 press | Starts auto-capture; another press during auto-capture requests force capture. During TTS, stops playback; press again to start next capture. |
| Pause/resume | Pause/Resume / `A` | `A` / GPIO27 press | Toggles current TTS playback. |
| Navigate reading | `R`, `N`, `P` | `R`, `N`, `P` in Pi terminal | Repeat, next, previous region. |
| Stop audio | Stop Audio | — | Stops document speech. |
| Quit | Exit / `Q` | `Q` in Pi terminal | Stops the relevant application. |
| Push-to-talk | Hold `G`, then release; or hold GUI Capture | Hold GPIO17 ≥0.7 s, release; Pi terminal `G` toggles start/submit | Only when Voice Input is enabled. Wait for **LISTENING**, speak English, release. |

With voice enabled, useful phrases include “repeat paragraph two”, “read from paragraph three”, “skip paragraph one”, “what is the heading?”, “read all headings”, “where am I?”, “continue reading”, and “voice help”. The **Voice** tab has a typed-command test field. The ASR is English `Systran/faster-whisper-medium.en` in an on-demand subprocess; it is not continuously listening or part of ordinary OCR latency.

In **Page tools**, optionally tick **Table-aware reading** and/or **Source-linked questions**. These use Microsoft Table Transformer detection/structure and `deepset/deberta-v3-base-squad2`, respectively, in separate workers after core processing/on request. Ask in **English**, by voice or by typing in Page tools; supported answers cite captured page text and can be read aloud. “Read table” and “Next row” are available there or by voice. A table marked **CHECK REQUIRED** must be verified visually; the tool intentionally refuses uncertain cell values. These optional features do not guarantee that every table or question is handled correctly. If a checkbox reports missing assets, follow the model-provisioning section; leaving it OFF keeps the normal path running.

## 6. Troubleshooting in the order it usually fails

| Symptom | Check |
|---|---|
| `Could not locate Project root` | Keep `1Pipeline` and `pics` as siblings under `Project`; do not move only Box10 to Desktop. |
| `ModuleNotFoundError: box6_audio` on Pi | Place all six Pi files together in `~/comms`; run from there. |
| PC says `WAITING FOR PI` forever | Check Pi `FYDP_PC_IP`, `ipconfig`, ping, Windows Private firewall TCP 9999, and that camera client is running. |
| Camera cannot open / black feed | Verify C525 is on the Pi, `v4l2-ctl --list-devices`, `/dev/video*`, USB power and OpenCV camera index 0. |
| Missing Paddle model | Restore the complete ONNX model directories or set `FYDP_PADDLE_MODEL_ROOT` before launching the GUI. |
| NLLB reports missing model/tokenizer | Restore `Project\nllb-200-1.3B-ct2` and cache the tokenizer separately; runtime is offline. |
| Piper reports missing asset | Restore/download both English `.onnx` and `.onnx.json` in `1Pipeline\piper_models`. |
| Voice says assets missing | Confirm `.venv_box7_voice\Scripts\python.exe` and `box9_models\faster-whisper-medium.en\model.bin`; then enable voice again. |
| Voice records but no answer | Check actual Pi HFP microphone, `--voice-devices`, correct GUI source, and TCP 10001. A voice command also requires **LISTENING** before speech. |
| Pi audio silent / broken pipe | Confirm **Audio(Pi)**, default Pi sink and `pw-cat`; test local Pi sound, then TCP 10000. If not needed, uncheck Audio(Pi) and use PC speakers. |
| Table or Q&A unavailable | Check `box8_models\table_detection`, `box8_models\table_structure`, `box9_models\qa` and `timm`/other worker dependencies. Enable the relevant Page tools checkbox. |
| `DLL load failed`, `libpaddle`, or Windows Application Control policy | Verify correct Python environment, `pip check`, VC++/CUDA libraries and security policy. Book Mode may disable itself while flat OCR still works. An application-control block cannot be fixed merely by reinstalling the same package. |

Keep the three TCP services on a private trusted network. The camera protocol uses Python pickle between the two endpoints; **do not expose TCP 9999 to untrusted hosts**. No credentials are required in source code or this README.
