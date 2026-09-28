# Box8 bounded acceptance validation — 13 September 2026

## Verdict

Source-linked paragraph questions worked on the selected saved captures. Simple ruled tables worked. Borderless-table header interpretation failed, so table mode is not yet dependable for arbitrary layouts. No production files were changed; SHA-256 comparison of the root Python files passed. No dependencies were installed.

## Executed cases

The paragraph samples are three differently captured/OCR-processed versions of the same English page, not three independent documents. These checks use the retained OCR regions, not a fresh OCR pass over those photographs. Table images were newly generated with known contents and processed by the existing PaddleOCR engine and actual local table detection/structure models. Questions ran through `Features.question` and its real local QA worker, with final speech recorded as text instead of played.

| Sample | Expected and actual results |
|---|---|
| Clean saved page | Camera location: “near eye level,” correct. Camera voltage absent: correctly refused. |
| Truncated saved page | Blur cause: “Motion from breathing and head movement,” correct. Error-rate detail missing from OCR: correctly refused. |
| Noisy/merged saved page | First difficulty: “Image acquisition,” correct. Halo cause: “Excessive sharpening,” correct. |
| Ruled electrical table | All 15 cells including headers matched after whitespace normalization. Correct processor voltage 12 V; correct highest-current row Radio, 4 A; correctly refused an absent purchase price. |
| Borderless timing table | All 15 original cell texts survived, but the header was incorrectly treated as a data row. Generated generic Column 1/2/3 labels and announced five data rows instead of four. Failed both lowest-delay comparison (Translation, 0.75 s) and OCR delay lookup (2.50 s). |
| Ruled mixed-unit table with blank | All 15 cells including the empty cell matched. Safely refused a mixed-unit comparison; no mA/A conversion is implemented. Answered the missing voltage as “blank,” which is grounded but less clear than “not specified.” |

Six of six paragraph checks passed. Table questions: four met the strict expected output, two failed, and one returned the truthful word “blank” instead of the requested abstention wording. Across 13 checks this is 10 strict passes, two functional failures, and one wording difference—not a general accuracy estimate.

Table questions were also confirmed using the production paragraph-grouping functions, rather than only individual OCR-fragment contexts. Outcomes were unchanged. The retained results include both passes for auditability.

## Measured latency

- Paragraph question to answer text: 4.05–4.41 seconds, including worker launch/model loading.
- Table OCR: 0.40–0.51 seconds per synthetic image; complete engine-loading call 1.89 seconds separately.
- Table detection/structure job: 4.51–4.53 seconds wall time per image, additional to OCR.
- Model-backed table questions with production paragraph context: 3.94–4.15 seconds.
- Numeric comparisons over an already reconstructed table: under 1 ms in this test; no additional QA model call.
- No runtime exceptions, job timeouts, or missing dependencies occurred.

These measurements exclude microphone recognition, TTS synthesis/playback, Pi transport and translation. They do not establish zero impact on normal-pipeline latency during simultaneous operation; that concurrency scenario was outside this bounded run.

## Findings worth addressing later

1. **Main functional issue:** borderless headers can be missed while `safe=True` and warnings remain empty. This damages column meaning, row numbering, reading and Q&A despite correct OCR text.
2. **Expected limitation:** mixed-unit comparisons abstain even when a human can convert the values (Processor 1 A exceeds 500 mA). This is safer than comparing raw numbers but restricts utility.
3. **Minor wording:** missing values are spoken as “blank.” Numeric answers can also repeat the row label and punctuation.
4. **Source precision:** noisy merged paragraphs retain broad evidence bounding boxes. Correct answer text does not guarantee a precise line-level highlight. Highlight rendering itself was not tested.

Good results: answer extraction tolerated the tested OCR noise; missing factual answers were refused; ruled tables preserved numerical values and blank cells; comparisons returned the correct source row where units were consistent.

## Artifacts and boundaries

`results.json` contains exact questions, answers, source regions, model tables, OCR fragments, timings, spoken output and hash evidence. `ruled.png`, `borderless.png`, and `mixed_units_blank.png` are the generated fixtures. Only this validation folder was edited.

No live GUI, Bluetooth microphone, Raspberry Pi, audible TTS, Urdu output, or concurrent normal-pipeline regression was tested in this run. No fixes were made.
