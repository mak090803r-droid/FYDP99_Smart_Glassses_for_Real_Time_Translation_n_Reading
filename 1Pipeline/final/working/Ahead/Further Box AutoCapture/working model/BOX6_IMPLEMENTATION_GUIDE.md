# Box6 implementation guide — completed local integration

Updated 8 September 2026.

## Current status

The previous incomplete checkpoint has been completed. Box6 now connects the audio router, durable control queues, capture detector, shared GUI/CLI session lifecycle and camera reconnection. Local validation is complete: 49 regression tests passed, 15 Python files compiled, real local models were exercised, and saved-image/video evidence was generated.

Read BOX6_REPORT.md for the measured results and limitations. Physical Pi audio, live C525 operation and a head-mounted user trial remain hardware acceptance checks; they have not been claimed as passed.

## Preserved originals and environment

- Do not modify or delete pipeline_cli_box5.py or piweb_cli2.py.
- Box5 SHA256: D66748988B9C917A158EA2F3A453B2C6D3BBCF122DC5ACC57D023125CAFF43EC
- Pi2 SHA256: 38B0D236E553DED37FD09A8AAE0DE092B47352BEE66F306086AB9E63027D0FEE
- Always use C:\Users\ali\Desktop\FYDP\fydp\Scripts\python.exe for Windows Python.
- No package installs, new environment, model downloads or cloud inference.
- Rules in Project/AGENTS.md remain applicable. No Gaussian blur was added.
- Working directory: C:\Users\ali\Desktop\FYDP\New\Project\1Pipeline\final\working\Ahead\Further Box AutoCapture\working model

## Completed integration checklist

1. AudioRouter instantiated, routed Piper modules registered, Audio(Pi) wired and cleanup connected.
2. ControlQueue wired into receiver, GUI/CLI, capture, speech and reconnect; terminal events remain latched.
3. CaptureDetector connected; exact scored-frame selection and three-distinct-frame stability retained.
4. Empty/stalled-camera handling, per-document retry and model-preserving reconnect implemented and tested.
5. OCR/UVDoc/NLLB/Piper loaders local-only; Book Mode lazily loads UVDoc.
6. Paragraph navigation, stable IDs, saved translation-to-audio mapping and timing separation implemented.
7. Real OCR, capture, translation, synthesis, GUI and loopback transport validation completed.
8. Report and evidence saved; Box5/Pi2 hashes rechecked unchanged.

## Files to keep together

pipeline_cli_box6.py, box6_runtime.py, box6_capture.py, box6_translation.py, box6_audio.py and piweb_cli3.py.

The host imports four helper modules. Copy BOTH piweb_cli3.py and box6_audio.py to the Pi when deploying Pi3; no remote deployment was performed here. Camera TCP remains 9999; optional audio uses 10000.

## Repeat the lightweight verification

~~~powershell
& 'C:\Users\ali\Desktop\FYDP\fydp\Scripts\python.exe' 'C:\Users\ali\Desktop\FYDP\New\Project\1Pipeline\final\working\Ahead\Further Box AutoCapture\working model\box6_validation\run_checks.py'
~~~

This writes regression_checks.json/log, compiles only the new files and checks protected baseline hashes. Additional saved-image scripts are in box6_validation; see the report for their outputs.

## Decisions that must not be lost

- Whole-paragraph NLLB input was tested and rejected because it dropped Chinese sentences. Final translation uses bounded sentence-sized units retaining region IDs.
- The Chinese-selected/English-document guard is deliberately narrow, not general language autodetection.
- No claimed OCR accuracy improvement: both matched saved images preserved Box5 text and segmentation. Known missed lines and over-grouping remain documented.
- UVDoc reduced recognized content on a tested flat page. Keep Book Mode off for flat A4 pages; no automatic quality-based unwarp selection exists.
- Recorded video validation sampled 12 downscaled frames; it was not a complete real-time wearable replay.
- Pi transport checks used a loopback fake speaker; the integrated real-Piper tests used a silent PCM sink.
- Existing helper *_STATUS.md files are historical handoffs. BOX6_REPORT.md and final JSON evidence supersede their old pending-test statements.

## Next acceptance gate

Perform the short physical PC/Pi audio and head-mounted capture checklist in BOX6_REPORT.md. This is hardware verification, not unfinished host integration. Keep Box5 available for rollback.
