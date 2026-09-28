# Box7 handoff and validation

Final local checks: 12 September 2026. Model comparison run: 11 September 2026.

## Delivered

- New host: `pipeline_cli_box7.py`; new Pi client: `piweb_cli4.py`.
- Optional GUI voice checkbox, OFF by default; Pi-earbud or PC microphone selection.
- Hold G in the PC GUI, or hold Capture for 0.7 seconds, then release to submit.
- Existing S/A, capture/pause buttons and navigation remain operational. With voice enabled, a short GPIO Capture press is acted on at release, allowing long-hold detection. Pi terminal G is a start/submit toggle because terminal input has no release events.
- Repeat current/numbered paragraphs, read from or skip a numbered paragraph, heading/title reading, counts, position, help, and continue reading.
- Spoken questions are transcribed and displayed, with a spoken Stage 2 notice. Source-grounded answers and tables are NOT implemented in this stage.
- Offline English Whisper small.en on the host CPU in a separate optional environment; no ASR model runs on the Pi. Existing OCR/NLLB/Piper settings remain intact.

Read `BOX7_README.md` for launch commands and the five files to copy together to the Pi. These files have not been remotely deployed or launched on the Pi.

## Automated results

| Check | Result |
|---|---|
| Existing Box6 suite | 51 passed |
| New Box7 voice suite | 36 passed |
| Existing host-behavior suite against Box7 | 25 passed |
| Total | 112 passed, 0 failures/errors/skips |
| Syntax compilation | All 13 new Python files passed |
| Synthetic Piper speech into real Whisper | 12/12 expected commands/questions recognized; silence rejected |
| Saved-image OCR comparisons | Identical text and region geometry on both retained image fixtures |
| Capture-gate comparisons | Identical decisions on empty, clipped-page and accepted-page debug frames |
| English and Urdu full page processing | Identical source text, translations, region records and exact TTS requests across Box6, Box7 OFF, and Box7 voice enabled/idle |
| PCM generation | Valid PCM with equal clip counts; output directed to a silent sink |

Tests include hold-versus-short-press behavior, default-off behavior, body-paragraph numbering, heading bookmarks, preserved original file hashes/core algorithms, stale-response cancellation, A-resume cancelling recording, QCY microphone selection and ambiguity errors, GUI text-field key guards, and actual local TCP host/Pi voice transport with a mocked microphone.

The first synthetic ASR run recognized 11/12 commands: "Skip paragraph one" became "Get Paragraph 1". A vocabulary prompt corrected the retained test case; the repeat run recognized 12/12. This is a small synthetic test, not a claim of 100% accuracy for real speech or accents.

One historical Box6 test references an absent OCR log. The runner supplies surviving archived OCR for that document in memory for that one test. No old log was recreated or altered. Tk printed a ThemeChanged warning during test-root destruction, but all test assertions passed.

Piper PCM hashes differ even when the unchanged baseline synthesizes the same input twice. The report retains that evidence; parity is based on exact speech requests, document results and valid audio generation, not an invalid requirement for byte-identical stochastic audio.

Evidence: `box7_validation/unit_checks.json`, `unit_checks.log`, `model_checks.json`, `model_checks.log`, and isolated `model_outputs`. Initial ASR evidence is retained separately as `model_checks_initial.json` and `.log`.

## Timing evidence and limits

Measured processing before audio, seconds, on the retained English page:

| Output language | Box6 | Box7 voice OFF | Box7 voice enabled, idle |
|---|---:|---:|---:|
| English | 0.778 | 0.698 | 0.739 |
| Urdu | 3.072 | 2.948 | 3.015 |

These are small, warm-run samples, not a statistical benchmark or a guaranteed speedup. There is no clear added page-processing delay in these samples. ASR is not invoked on the ordinary OCR/translation path. When enabled, lightweight control/connection handling and a previously loaded idle model can still consume resources; literal zero overhead is not promised.

In the successful synthetic ASR run, request-to-result time was 2.48 seconds for the first request and approximately 1.41–1.47 seconds thereafter, excluding recording duration. A first-ever setup run took much longer (about 55 seconds); cold model/import/cache startup can vary. Voice requests are rejected during capture/OCR/translation, and new captures cancel active recognition. No physical Bluetooth transfer or speaker latency was measured.

## Protected baseline

SHA-256 values match those recorded before implementation:

```text
pipeline_cli_box6.py  99D33D8AFB07497872B40C09A822254C65298AEC2DEAA0FBC1AE646BABDC1747
piweb_cli3.py         93DDD141345E3E350E6F2EE30C06839141CD75388B3C524083AD24633C58214C
box6_audio.py        F4FF6FBA8BE69D1C22D5099EA5A6C28A6E805DA1E040AC34500BC4D09AD7589E
```

## Remaining physical acceptance checks

1. Copy all five Pi-side files listed in the README, launch the new host/client, and confirm camera 9999, audio 10000 and voice 10001 connections.
2. Select the QCY headset/HFP/HSP profile on the Pi; check `python piweb_cli4.py --voice-devices`. Confirm BOTH microphone recording and TTS playback work. Bluetooth microphone mode can reduce playback fidelity.
3. With voice OFF, verify normal capture, pause/resume, keyboard shortcuts and representative English/Urdu page reading.
4. Enable voice. Short-press Capture, then test long-hold without an accidental capture. Wait for the ready cue/LISTENING, speak, and release. Verify microphone readiness and cue audibility on the real audio backend.
5. During playback, test numbered paragraph repeats, headings and continue. Preview commands return to the saved paragraph, not the exact interrupted word. Ordinary pause/resume preserves the existing clip position.
6. Test A during recording, new capture during recognition, disconnect/reconnect, disabling voice, background noise and your accent. Confirm incorrect/out-of-range requests do not read the wrong numbered paragraph.

Local implementation and regression validation are complete. Live Pi/QCY/GPIO/camera acceptance is still outstanding; saved images, silent audio and mocked-microphone tests do not replace it.
