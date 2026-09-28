# Box6 translation handoff status

> Historical handoff. Integration and local validation were completed on 8 September 2026. See ../BOX6_REPORT.md and regression_checks.json for final results; pending-test statements below describe the earlier checkpoint, not the current state.

Status at requested model-switch pause: helper implemented; real NLLB comparison has NOT completed. No model/GPU process remains running.

## Implemented

- `../box6_translation.py` provides `translate_regions(paragraphs, language, output_language, translator, tokenizer) -> (copied_regions, elapsed_seconds)`.
- Keeps `region_id`, bounding boxes and line data; adds effective source language, translation status, chunk count and review warnings. Duplicate IDs fail visibly.
- Preserves paragraph context when <=400 source tokens. Oversize paragraphs split at sentence/word/character boundaries without losing non-whitespace source content.
- Explicit CTranslate2 settings: beam2 (actual Box5/CT2 4.8 default), maximum source400, output512 (Box5 default256), batches8. Empty/missing/capped output fails visibly instead of foreign-source fallback to English speech.
- Chinese-selected OCR can stay selected while translation recognizes a long, overwhelmingly Latin document with strong English function-word evidence; bypasses English-to-English translation. No French/Spanish guessing. Script mismatch diagnostics cover Han, Hangul and Arabic script.
- Numeric-only regions and known short identifiers bypass NLLB. Review diagnostics flag changed digits/identifiers; no invented replacements or claimed accuracy metric.
- Last static refinement ensures `STOP THE ENGINE` and ordinary `real-time` are translated rather than treated as codes. This final refinement has a new test but has not been rerun since the pause.

## Verified

`test_translation.py`: 10 no-model tests passed before the final acronym refinement. Covered Unicode content preservation, long no-space strings, bounded chunks, stable IDs, original dictionary preservation, source resolution on the actual Sept3 saved page, uncertain French non-switch, numeric bypass, empty/result-count/cap failures. The latest suite contains 11 tests; rerun it.

## Real benchmark state

`benchmark_translation.py` was launched once, exited BEFORE model loading, because the first parent folder with `pics` was `final/working` rather than project root. The predicate now requires both `pics` and `1Pipeline`. No second launch was made because the user requested a model-switch pause. Therefore there are no validated real-model speed/quality improvements yet and no translation_benchmark.json from this test.

The benchmark extracts the exact Box5 translation functions via AST (does not launch GUI/camera/audio), loads only the cached tokenizer and local CT2 model, warms once, compares saved French (2026-07-23 14:31:13), saved Chinese (2026-07-23 14:26:29), Sept3 English with Chinese OCR selected, and three English-to-Urdu regions. Real French/Chinese records remain in `Project/1Pipeline/final/working/ocr_results_comparison.txt`; standalone July OCR files have moved/disappeared.

## Concrete baseline evidence

- `paragraph_test_outputs/run_001_20260903_134627_ocr.txt`: selected Chinese, OCR entirely English. P1's English source is reduced by translation to its final part. New helper bypasses translation on this page and preserved exact source in the no-model test.
- `paragraph_test_outputs/run_004_20260819_142053_ocr.txt`: sole OCR source `1`, NLLB output `1 and 2`. New helper keeps `1` without invoking model.
- Aug6 saved French-selected pages also contain English, and their translations omit phrases. Current helper auto-corrects only the Chinese-selected case as authorized; it intentionally does not broaden Latin-language selection changes yet.
- English-to-Urdu run_001_20260806_211116 source has `thirty-four percent`, saved translation has `چوبیس فیصد` (24%). Digit-only diagnostics do not catch number-word changes; flag as limitation, not solved accuracy.
- Box5 model loaders may invoke model conversion/downloads and tokenizer network access. Parent integration must enforce local loading and remove auto-download paths.
- Box5 TTS consumes Q/disconnect and clears event queues, so main can miss these terminal events. Parent is handling controls/recovery.

## Paragraph reading order audit

Current row merge uses axis-aligned `height` with centre tolerance `0.30 * median_height`, even though real glyph height comes from rotated polygons. Tall skewed boxes can make neighboring rows merge. It can also merge separate columns within `6 * median_height`. Grouping then globally sorts centre_y, so two-column pages interleave. Actual Aug6 run_001_20260806_210839 has source starting `ordinary tool ... A wearable ... Field Evaluation ...` and one region; baseline order is clearly suspect. However exact polygons were not replayed yet, so no grouping change has been implemented here. Preserve single-column behavior until capture agent's actual OCR replay yields polygons; test row-preservation and full text sequence against printed source before adjusting.

## Exact commands to resume

Run in PowerShell, no pip installation:

```powershell
& 'C:\Users\ali\Desktop\FYDP\fydp\Scripts\python.exe' 'C:\Users\ali\Desktop\FYDP\New\Project\1Pipeline\final\working\Ahead\Further Box AutoCapture\working model\box6_validation\test_translation.py'
& 'C:\Users\ali\Desktop\FYDP\fydp\Scripts\python.exe' 'C:\Users\ali\Desktop\FYDP\New\Project\1Pipeline\final\working\Ahead\Further Box AutoCapture\working model\box6_validation\benchmark_translation.py'
```

Coordinate GPU exclusive use with the capture/OCR validation agent. After benchmark inspect `translation_benchmark.json` full outputs, not timings alone. Do not claim BLEU/CER/overall translation accuracy from these comparisons without verified human references. Paragraph context may improve coherence while increasing decoding time; decide based on exact source/output omissions and usable latency.
