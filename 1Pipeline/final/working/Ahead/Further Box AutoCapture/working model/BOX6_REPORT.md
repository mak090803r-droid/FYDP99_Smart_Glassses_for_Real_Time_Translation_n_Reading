# Box6 implementation and validation report
Date: 8 September 2026

## 10 September startup correction

The real GUI failure was reproduced from the latest debug logs: Windows first
blocked `pandas._libs.indexing` during PaddleX eager repository initialization,
then retrying in the same process returned `PDX has already been initialized`.
Box6 now sets `PADDLE_PDX_EAGER_INIT=False` before third-party imports and checks
the native DLL before importing PaddleOCR. OCR inference does not require the
unrelated eager repository scan. A blocked DLL therefore produces an actionable
error without poisoning PaddleX for another GUI attempt.

Validation after this correction: 51 tests passed, all 15 new Python files
compiled, and two consecutive real PaddleOCR initializations completed in one
fresh process with the detection-only adapter available. Box5 and Pi2 hashes
remain unchanged. An already-open Box6 process still contains the old imported
code and must be closed once before using the corrected file.

## Outcome

Box6 host integration and offline validation are complete. Box5 and Pi2 remain byte-for-byte unchanged. Box6 is ready for a supervised hardware trial, not a claim of perfect capture, translation, or verified Pi speaker operation.

The project was resumed from BOX6_IMPLEMENTATION_GUIDE.md. Existing Box6 helpers were integrated rather than rebuilding the pipeline. No packages, environments, or model downloads were added.

## What changed

### Capture and reliability

- Live quality analysis reuses the already-loaded Paddle detection model without recognizing every word. Final captured-image OCR still uses the full recognizer. A compatible full-OCR fallback handles a detector API failure.
- Original text-envelope geometry and focus/lighting/distance thresholds are preserved. Capture still requires three distinct good observations in the latest four, plus the existing stable-time requirement. One soft failure is tolerated; confirmed clipped/unreadable text clears progress.
- The exact scored frame is captured. Quit, disconnect, and cancellation are checked even before any frame arrives. Stalled frames cannot be force-captured; camera silence causes reconnect rather than indefinite waiting.
- Camera reconnection reuses loaded models. A document/OCR/audio failure returns to retry instead of terminating the session. Corrupt/oversized camera messages fail explicitly; the existing camera framing remains compatible.
- Fixed a Windows redirected-console Unicode failure that could otherwise abort a successful capture when printing a status symbol.
- Receiver, listener and audio cleanup are connected to session shutdown. The audio port can be reused by a subsequent session.

### OCR and paragraph tracking

- Active preprocessing remains raw grayscale, without Gaussian blur or a newly imposed sharpening/thresholding filter.
- Optional UVDoc operates in memory, avoiding the shared temporary JPEG. Empty/invalid outputs raise a recoverable error. UVDoc is loaded only when Book Mode is enabled.
- Recognition generators are consumed while holding the OCR lock.
- Existing line merging, paragraph grouping, heading classification and overlay functions remain unchanged; AST parity is tested.
- Stable region IDs connect source text, box, translation, final spoken text and playback events. A per-capture *_box6.json mapping is written before speech, so a playback failure does not lose the document evidence.
- Processing-before-audio time is reported separately from total time including speech.

### Translation

- Cached local NLLB/tokenizer assets are required. OCR and UVDoc use explicit local model paths; missing assets fail locally rather than initiating downloads.
- Chinese-selected OCR on a long, strongly English document no longer sends that English text to NLLB as Chinese. The OCR model remains unchanged. This is a narrow safeguard, not general automatic language detection.
- Numeric-only labels and recognized standalone identifiers bypass NLLB. Ordinary uppercase headings are still translated.
- Source text remains untouched in the logs. Translation-only normalization removes artificial spaces between Chinese characters, normalizes four-digit Chinese year notation, and converts English number words before a small whitelist of units to digits.
- Translation uses bounded sentence-sized requests associated with their original region IDs. Explicit limits: 400 source tokens, 512 output tokens, beam 2, batches of at most 8. Missing, empty, or visibly capped output raises an error instead of speaking an unmarked partial result.
- A whole-paragraph translation experiment was rejected: real Chinese output dropped final sentences. The final implementation retains smaller requests. It does not promise full-page contextual reasoning.
- Changed numerical/identifier information is flagged for review, not guessed back into the output. Existing conservative English SymSpell behavior is retained.

### Audio, controls and Pi3

- The existing GUI gains Audio(Pi); its layout is otherwise preserved.
- Unchecked uses PC audio. Checked routes document TTS, positioning guidance and tones to Pi over separate TCP port 10000. Camera traffic stays on 9999.
- A route change stops the previous sink and restarts the currently active speech region on the selected sink. A missing Pi audio connection is reported; it does not silently play on the PC.
- Piper synthesis remains on the PC. Pi receives bounded PCM audio and acknowledges actual playback completion before the host advances the region.
- A pauses/resumes, S stops speech, R repeats, N advances, P goes back, Q quits. S while idle starts capture; during guidance S force-captures a fresh frame. The existing GPIO capture/pause actions are retained.
- Canceled synthesis cannot revive old speech. Repeat audio uses a bounded cache. Optional prefetch support exists in the helper; the host does not currently schedule speculative next-paragraph synthesis.
- Pi3 is a copy of the existing Pi2 client with audio/control/recovery additions. Camera properties, resolution, JPEG quality and preprocessing were not retuned.

## Validation completed

All Windows runs used:
C:\Users\ali\Desktop\FYDP\fydp\Scripts\python.exe

| Check | Result |
|---|---|
| Automated regressions | 49 passed, 0 failed, 0 skipped |
| Syntax compilation | 15 new implementation/validation Python files passed |
| Protected baselines | Box5 and Pi2 SHA-256 unchanged |
| Capture benchmark | Six saved frames; all detector polygons and compared assessments matched full OCR |
| Recorded video | 12 sampled frames processed through capture analysis |
| OCR regression | Two matched images: identical Box5/Box6 text and region structure |
| Real local models | OCR, UVDoc, English Piper and Urdu Piper succeeded with socket connections blocked |
| Integrated saved-image runs | OCR through translation handling, region tracking, and real Piper synthesis passed for English output and English-to-Urdu output |
| GUI / controls / transport | Withdrawn Tk wiring, loopback PCM/acknowledgment, cancellation, navigation, reconnect, retry and cleanup passed |

Audio tests used a silent/fake output sink. They did not test a physical speaker, Pi, or wearable camera.

### Capture speed

Final benchmark, three repetitions per saved frame:

| Detector step | Median of per-image medians |
|---|---:|
| Full OCR used for live detection | 381 ms |
| Detection-only | 37.6 ms |
| Ratio | 10.14x faster |

This is the detector step, not a 10x whole-pipeline speedup or a measured 10x improvement in time-to-capture. Thresholds were not loosened to achieve it. Five benchmark inputs were 1920x1080 and one was 1280x720. The sampled diagnostic video was 640x360; it is not native-resolution focus ground truth. Video was sampled, not played through a complete real-time camera/audio session. Temporal-gate behavior was tested separately with controlled frame sequences.

### OCR findings

Compared with the matched title plus three body paragraphs from the printed English source document:

| Saved input | Box5 CER / WER | Box6 CER / WER |
|---|---:|---:|
| September 3 capture | 6.97% / 14.56% | 6.97% / 14.56% |
| August 6 analysis frame 70 | 5.80% / 11.32% | 5.80% / 11.32% |

These are case-sensitive, punctuation-preserving edit-distance measurements on two images, not a project-wide accuracy estimate. Chinese OCR selection on the English pages was intentional to reproduce the user's saved setup.

The September image still omits initial body lines. The August frame still has reading-order/over-grouping issues (two regions instead of the expected title and three paragraphs). This iteration does not claim to fix those baseline segmentation problems. No unsupported accuracy percentage has been invented.

UVDoc on the tested flat page produced 1,820 characters versus 2,312 without it. Character count alone is not accuracy, but the loss is a clear reason not to enable Book Mode indiscriminately. No automatic raw-versus-unwarped quality selection has been added.

### Translation comparison

Real local model measurements on saved OCR text; small, non-statistical samples:

| Case | Box5 | Box6 |
|---|---:|---:|
| French to English | 1.943 s | 2.136 s |
| Chinese to English | 0.950 s | 1.389 s |
| English page with Chinese selected | 2.944 s | 0.0012 s (correct bypass) |
| English to Urdu fixture | 1.214 s | 1.299 s |

Specific observed corrections include Chinese year 2025 instead of 2005, better association of the 34% solar reduction and 8-degree temperature rise, and preservation of 34 rather than the previously observed Urdu 24. These come from the tested notation normalization and source-language guard; they are not a general translation-accuracy guarantee.

Technical errors remain: for example, Chinese frames can become lines and Urdu can confuse a mechanical fan with an admirer. French technical terminology also remains imperfect. Translation has modest additional overhead on these non-bypass examples. BLEU/COMET or an overall accuracy percentage was not asserted without verified target-language references.

### Complete saved-page runs

A real 2,312-character OCR page passed through region mapping and Piper synthesis:

- English output: four region IDs retained; 1.63 s before audio; about 138 s of generated PCM including prompts/tones.
- Urdu output: four region IDs retained; 4.88 s before audio; about 162 s of generated PCM including prompts/tones.

The test sink consumed PCM immediately. Recorded TTS-stage test time measures synthesis/control overhead, not how quickly a person hears the page. Do not present those test wall times as real spoken-page latency.

## Files and evidence

Keep these six implementation files together in the working model directory:

- pipeline_cli_box6.py — integrated PC entry point.
- box6_capture.py — detection-only adapter.
- box6_runtime.py — compatible frame decoding and durable controls.
- box6_translation.py — bounded translation and region tracking.
- box6_audio.py — host/Pi audio protocol, playback workers and routing.
- piweb_cli3.py — new Pi client.

Evidence under box6_validation:

- regression_checks.json and regression_checks.log — final test results and hashes.
- capture_replay_final.json — final six-frame benchmark and 12 video samples.
- ocr_regression.json — source reference, both OCR outputs and CER/WER.
- translation_benchmark.json — full source and Box5/Box6 translation outputs.
- local_models_smoke.json — real offline model/synthesis check.
- end_to_end_saved.json — integrated model-backed runs.
- end_to_end_outputs — isolated test captures, overlays, OCR text, mapping JSON and console log. These test runs did not append to the user's normal logs or captures.

## Launch and hardware acceptance

Run the PC entry point using the existing environment:

~~~powershell
& 'C:\Users\ali\Desktop\FYDP\fydp\Scripts\python.exe' 'C:\Users\ali\Desktop\FYDP\New\Project\1Pipeline\final\working\Ahead\Further Box AutoCapture\working model\pipeline_cli_box6.py'
~~~

The GUI defaults to PC audio. Existing Pi2 can still supply the compatible camera stream for PC-audio trials. To test Pi sound, copy BOTH piweb_cli3.py and box6_audio.py into the Pi's existing comms folder and run Pi3 using its existing Pi environment. No SSH upload or remote process replacement was performed.

Pi3 defaults to PC address 192.168.137.1; FYDP_PC_IP can override it. FYDP_PI_AUDIO_DEVICE selects the Pi output when required. The audio helper uses existing sounddevice or an existing ALSA aplay backend. Port 10000 must be reachable on the local/private network in addition to 9999. No firewall settings were changed. This is a trusted-LAN protocol, not authenticated or encrypted transport.

Before replacing Box5 for a demo:

1. Use a flat single-column page, Book Mode OFF, and PC audio first. Confirm all source lines and paragraph boxes, not just high confidence.
2. Verify A pause/resume, S stop, S recapture, R repeat, N/P navigation and Q while waiting for the Pi.
3. With Pi3 running, select Audio(Pi). Verify exclusive Pi playback, pause/resume, route switching mid-paragraph and correct paragraph advancement.
4. Disconnect/reconnect the stream and audio device deliberately, then retry. Finally do a head-mounted audio-only capture trial.

For a clean demo, preload models before presenting, use one short paragraph for the first demonstration, show source-region highlighting with the spoken translation, and label confidence separately from measured accuracy. Keep Box5 as the rollback option. Do not promise perfect technical translation, arbitrary multi-column layout support, or complete-text capture when unseen content never entered the camera frame.

## Protected baseline hashes

Box5:
D66748988B9C917A158EA2F3A453B2C6D3BBCF122DC5ACC57D023125CAFF43EC

Pi2:
38B0D236E553DED37FD09A8AAE0DE092B47352BEE66F306086AB9E63027D0FEE
