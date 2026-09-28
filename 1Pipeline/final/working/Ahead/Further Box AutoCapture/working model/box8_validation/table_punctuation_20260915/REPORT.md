# Table-cell decimal punctuation repair — 15 September 2026

## Result

The exact failing Page 2 capture now returns `97.4%`, `93.1%`, `88.7%`, and `81.9%`. The table is `safe=true` with no ambiguity warnings. No value is hard-coded and the ambiguity validator remains enabled.

## Root cause

The initial optional table OCR operates on an externally enlarged, rectified grayscale table. Paddle's text detector returned `93 1%` and its word box did not provide reliable decimal evidence. The previous fallback re-cropped that detected word box, so it reused already clipped/resampled evidence. More enlargement and CLAHE could not restore a dot that was no longer represented reliably.

The repair retains the aligned full-resolution colour page only while Table-aware reading is ON. For a suspicious numeric cell, it crops the complete cell using grid boundaries and runs the recognition model directly, bypassing a second text-detection box. It tries small conservative boundary insets/padding. A correction is accepted only if at least two results above 0.80 confidence agree on a decimal-form percentage and its digit sequence is identical to the original. Otherwise the original remains and the existing warning stays.

For `93 1%`, six high-confidence full-cell retries independently returned `93.1%`; the strongest was 0.9993. The recovery took 1.641 seconds. Retry evidence and reason are retained in `cell_ocr_retries` and emitted as `original OCR -> retry OCR -> final value/reason` in Box8 debug output.

## Files changed

- `pipeline_cli_box8.py`: retain aligned full-resolution colour input only when the table toggle is ON and attach it to the optional document context.
- `box8_features.py`: send that image to the optional table subprocess and emit concise retry diagnostics.
- `box8_worker.py`: targeted padded full-cell direct-recognition fallback and conservative consensus.

No Box6, Box7, Pi client, translation, normal TTS, capture gating, or ordinary OCR algorithm was refactored.

## Verification

- Exact Page 2 real capture: all four values matched; `93 1% -> 93.1%`; READY/no warnings.
- Existing automated regression: 151 passed, zero failures/errors/skips.
- Syntax compilation: passed.
- Toggle comparison: Box8 without a feature controller, Box8 OFF and Box8 ON produced identical normal OCR text, essential paragraph records and OCR image. The colour table source was absent OFF and retained ON only. The unchanged core behavior is also covered by the existing Box7/Box8 regression suite.

The exact worker took 20.889 seconds including detection, reconstruction, worker/model startup and three suspicious-cell retries. The `93.1%` retry itself took 1.641 seconds. This cost occurs only when Table-aware reading is ON and only suspicious cells invoke retries.

## Remaining issue

Very degraded punctuation may still lack two agreeing high-confidence readings. In that case it intentionally remains CHECK REQUIRED rather than guessing. The unrelated French-source selection/translation issue is unchanged.
