# Optional table repair — 15 September 2026

## Outcome

Three September 15 photographs now produce exact table contents: 55/55 cells including headers, or 44/44 data cells. These are three captures of two document designs. The fourth photograph, the September 14 evaluation form, is detected as an uncertain ruled table; its skewed/partly obscured grid is not yet reconstructed reliably. It requests recapture rather than inventing blank cells or reading scrambled rows.

| Capture | Result | Latest worker wall time |
|---|---|---:|
| 001, September 15 | 4 headers, 4 rows; 20/20 cells correct | 17.07 s |
| 003, September 15 | 3 headers, 4 rows; 15/15 cells correct | 14.41 s |
| 004, September 15 | 4 headers, 4 rows; 20/20 cells correct | 7.73 s |
| 005, September 14 | Ruled table flagged CHECK; spoken recapture warning | 4.67 s |

Times include subprocess startup, model loading and optional crop OCR. They are not normal OCR/translation stage timings. Scheduling, cold starts and concurrent machine load affect these numbers; they are not a controlled speed benchmark.

## Changes

- Added independent grid detection and perspective-rectified cell assignment, so ruled-table detection no longer depends entirely on the learned detector exceeding 0.80.
- Added local CPU OCR on rectified table crops, only in the optional worker. Two contrast-enhanced crop OCR scales must agree before restoring a missing decimal point; digits must remain identical. No sample values are hard-coded.
- Tightened missing-header, clipped-fragment, missing-row-label and ambiguous-number checks. Blank images are rejected before model detection.
- Preserved surrounding prose when OCR merges a paragraph with table headings. Table rows are spoken once with column labels; original OCR regions are not rewritten. The GUI displays the table boundary and READY/CHECK result when analysis completes.
- Turning tables off cancels active table work and clears its results. An epoch check rejects stale results even if the toggle is switched back on before an older job finishes.
- Reliable ruled grids use the first row as column labels. This is an explicit convention, not general semantic header detection; headerless and complex merged-cell layouts remain limitations.

Modified only Box8 helpers: `box8_worker.py`, `box8_tables.py`, `box8_features.py`, `box8_gui.py`; added `box8_grid.py`. Pre-change helper copies are in `before/`. SHA-256 checks confirmed `pipeline_cli_box6.py`, `pipeline_cli_box7.py`, `pipeline_cli_box8.py` and `piweb_cli4.py` unchanged. No new packages or models installed.

## Speech validation

Executed `Features.table_command` with the real local Piper synthesizer and saved non-silent WAVs for all four cases. Successful tables announce their dimensions, then repeat the column label for each value:

> Row 3. Condition: Curved page; OCR: 88.7%; Table: Detected; Structure: Moderate.

The generated numeric-row audio was transcribed by the existing local Whisper recognizer as: “Row three, condition, curved page, OCR, 88.7%, table, detected, structure, moderate.”

Automatic playback checks also verified that the real capture's surrounding prose remains present, all four percentages occur exactly once, and the old `88 7%` text is not spoken instead of the corrected table value.

## Tests and limits

- Existing regression suite: 151 passed, no failures/errors/skips.
- Added checks: 11 passed, covering exact matrices, difficult-form refusal, OFF/no worker, cancellation, stale results, clipped cells, missing headers/labels, uncertain decimals and automatic reading.
- Real blank and real prose negative controls: no tables returned.
- Actual suspended optional workers: 0.0 measured CPU seconds over each 0.3-second observation, then resumed successfully.
- A Tk teardown warning appeared during the existing GUI tests, without a test failure.

OFF creates no table worker and performs no table crop OCR. ON adds optional CPU work; this does not change the main OCR/translation algorithms, but zero CPU contention or identical live pipeline latency is not guaranteed. A full English/Urdu/Pi hardware session was not rerun. WAV synthesis and one ASR round-trip are software validation, not proof of Bluetooth playback.

## Use

Restart the PC Box8 application to load the updated helpers. In Page tools, enable Table-aware reading and capture again. The toggle remains OFF by default. No Pi client update is needed. For an already captured page, use Read table after its status is READY. CHECK means the current capture should not be trusted as a structured table.

The previous French/English translation mismatch is separate and was not changed by this table repair. Choose the correct source language.

Evidence: `model_results.json`, `speech_results.json`, `speech_roundtrip.json`, `fix_tests.json`, `negative_results.json`, and `unit_checks.json`; WAVs and image fixtures are in this folder.
