# Capture audit handoff — 2026-09-08

> Historical handoff. Integration and local validation were completed on 8 September 2026. See ../BOX6_REPORT.md and regression_checks.json for final results; pending-test statements below describe the earlier checkpoint, not the current state.

Paused at the parent's request for a model switch. No OCR/GPU benchmark has
been launched by this capture agent. Do not describe the proposed detector
optimization as benchmarked or proven yet.

## Protected source and environment

- Box5 baseline was not modified.
- Main Box6 was not modified by this agent.
- All Python calls used `C:\Users\ali\Desktop\FYDP\fydp\Scripts\python.exe`.
- No package installations, camera sessions, GUI launches, or live audio tests.
- Project AGENTS.md was read. Preserve local models, existing three-frame
  temporal gate, and no Gaussian denoising on OCR input.

## Completed evidence

The current `working model/paragraph_test_outputs/live_debug_sessions` has
nine sessions containing **306 recorded analyses**. Events show **6 automatic
captures and 1 forced capture**. This is an event count, not a capture success
rate: recordings do not identify every intended attempt as an independent trial.

| Guidance state | Recorded analyses |
|---|---:|
| look_down | 100 |
| hold_still | 87 |
| look_up | 29 |
| page_not_found | 26 |
| bad_lighting | 25 |
| almost_ready | 25 |
| move_back | 12 |
| glare | 1 |
| checking | 1 |

Only one recorded analysis was stale. Typical session median analysis age was
0.45–0.53 seconds. There is no evidence here for blindly lowering the focus,
lighting or alignment thresholds.

Visually inspected Aug6 session `session_20260806_210058`:

- `analysis_frames/analysis_00070.jpg`: all printed text is visible; ready
  decision is appropriate (37 rows).
- `analysis_frames/analysis_00099.jpg`: final printed lines genuinely extend
  below the image; look_down rejection is appropriate (34 rows).
- `analysis_frames/analysis_00008.jpg`: the selected region is an unfocused
  computer monitor, not a valid sparse document. It passes the live gate once
  but full-frame LapVar is only **21.38**.

Computed full-resolution LapVar on all 306 stored raw analysis JPEGs: 74 are
below 100; only one of these passed the recorded live gate (the monitor above).
All visually valid recorded ready-page examples pass the current post-save
threshold. **Do not remove the post-save threshold based on this dataset.**

Latest `run_001_20260903_134627_ocr.txt` has a title and three body paragraphs,
but its first body paragraph starts `able to face a printed page...`; the
capture visibly includes the preceding two printed lines. This is a real
omission to investigate at OCR/detection level. The log language is Chinese
while the document is English, intentionally selected by the user in earlier
testing. The overlay is unchanged grayscale geometry, so this example does
not appear to have been unwarped.

Baseline preprocessing is **raw grayscale only (V_A)**. The unsharp helper is
unused; the active path does not perform CLAHE/gamma. Existing old comments
must not be mistaken for current behavior.

Baseline failure risks already sent to parent:

- Capture loop waits on `frame is None` before draining quit/disconnect events.
- No explicit stale incoming stream safeguard; forced capture can use an old
  latest frame.
- UVDoc path assumes a valid result and writes/reloads shared temporary JPEG
  without guaranteed cleanup, adding avoidable encode/decode loss and I/O.
- Every live capture analysis executes full OCR recognition although only
  detector polygons are consumed.

## New files prepared

- `../box6_capture.py`: `CaptureDetector(ocr)` adapter that reuses the loaded
  PaddleX `text_det_model` and `get_text_det_params()`, with full-OCR fallback.
  Call under the existing OCR lock. Source inspection confirms the installed
  PaddleX auto-parallel wrapper forwards these attributes; live inference is
  **not yet tested**. Full final OCR must retain the original engine.
- `replay_capture.py`: six saved 1080p images, two timed repeats per engine,
  exact polygon-set equality, complete capture-assessment equality, and
  optional sampled recorded video. It uses cached prediction outputs when
  comparing downstream assessments, avoiding extra GPU calls.
- `ocr_regression.py`: runs Box5 and Box6 full OCR on two matched English-page
  captures; evaluates against the title plus three body paragraphs from
  `Project/1Pipeline/final/working/ground_truth/English_OCR_Test_2x16pt_2x18pt.docx`.
  Default Chinese OCR setting reproduces the intentionally mixed saved run.

All three prepared Python files passed `py_compile` on 2026-09-08. No measured
speedup, OCR improvement, or video replay result exists yet.

## Resume commands

First coordinate GPU access with the translation agent; use one heavy model
test process at a time. Set the working directory to:

`C:\Users\ali\Desktop\FYDP\New\Project\1Pipeline\final\working\Ahead\Further Box AutoCapture\working model`

```powershell
& 'C:\Users\ali\Desktop\FYDP\fydp\Scripts\python.exe' '.\box6_validation\replay_capture.py' --pipeline '.\pipeline_cli_box5.py' --language chinese --repeats 2 --video '.\paragraph_test_outputs\live_debug_sessions\session_20260903_134521\live_feed.avi' --video-samples 6 --output '.\box6_validation\capture_replay_box5.json'
```

Require `all_polygon_sets_equal` and `all_assessments_equal` before integrating
the adapter. Confirm `mode=detection_only`, because a fallback cannot establish
the claimed optimization. Report median warm inference timing, not model load.

```powershell
& 'C:\Users\ali\Desktop\FYDP\fydp\Scripts\python.exe' '.\box6_validation\ocr_regression.py' --language chinese --output '.\box6_validation\ocr_regression_chinese_setting.json'
```

Optionally repeat with `--language english` to test the appropriate source
language model. Do not compare timings across concurrent GPU processes. The
recorded video is **640x360 at target 5 FPS**, while raw capture inputs are
1920x1080. Its geometry/guidance replay is useful, but its focus metrics cannot
be used to calibrate native-camera thresholds.

The full OCR regression currently covers book mode off. A separate actual
UVDoc check remains necessary for the new in-memory book-mode path; ensure
empty/invalid UVDoc outputs fail clearly without corrupting paragraph state.

## Remaining engineering choices

1. Verify detector-only polygon and gate equivalence plus latency savings.
2. Integrate adapter into Box6 capture only after passing that check; final OCR
   must continue to use complete OCR and keep stable paragraph IDs.
3. Investigate the two missing top body lines with proper language selection,
   detection outputs and matched reference. Do not enable sharpening or
   binarization based on character-count/confidence gains alone.
4. Preserve baseline quality thresholds until controlled evidence supports a
   change. Existing logs largely confirm genuine clipping during alignment.
5. Test parent fixes for zero frames, disconnect, stalled stream, forced
   capture age, and clean UVDoc failure handling with simulated components.
