# Box8 completion report — 13 September 2026

Local implementation and validation are complete. Run `pipeline_cli_box8.py` on
the PC and keep the existing `piweb_cli4.py` on the Pi. No Pi5 was necessary.
The optional switches are in the new **Page tools** tab and default to OFF.
See BOX8_README.md for launch instructions, controls, model setup and limits.

## Preservation

SHA-256 tests confirm these existing files were not changed:

- `pipeline_cli_box7.py`, `piweb_cli4.py`
- `box7_reading.py`, `box7_voice.py`, `box7_gui.py`, `box7_commands.py`

The existing regression tests also protect Box6 and its audio helper. Box8 adds
hooks for existing raw OCR metadata, optional work scheduling, commands and GUI.
Capture gates, preprocessing, paragraph grouping, OCR model settings, ordinary
NLLB translation and Piper voice settings are preserved. New evidence uses a
`_box8.json` suffix. Existing test logs were not overwritten.

## Verified locally

| Test | Result |
|---|---|
| Existing Box6 suite | 51 passed |
| Existing Box7 suite | 37 passed |
| New Box8 tests | 25 passed |
| Existing host behavior against Box8 | 25 passed |
| Box8 voice transport/control regression | 13 passed |
| Total automated regression | 151 passed; no failures/errors/skips |
| Real Piper speech → Whisper → new command parser | 6/6 expected commands/questions |
| Real Q&A model | Correct voltage/cleaning answers; refused missing price/inventor answers |
| Real table models + actual existing OCR | Three correct headers, four correct data rows, all 12 data cells matched the clean ruled-table fixture |
| Blank-page table test | No table returned |
| Real answer → English/Urdu Piper | Both produced valid PCM; reading continuation returned the expected bookmark |
| Actual process scheduling | Suspended worker used 0.0 measured CPU seconds over 0.3 s, then resumed and answered correctly |
| Saved-page full OCR/NLLB/Piper runs | Identical OCR/translation/region records and exact normal speech requests across Box7, Box8 OFF and Box8 ON |

Table ambiguity checks cover missing structure, merged/spanning cells, OCR
crossing cell boundaries, low-confidence text and unsafe numeric comparisons.
Playback tests cover source references, row/column selection, automatic return to
reading, unchanged paragraph data, Stop precedence and stale-result cancellation.
GUI tests verify default-off switches and typed requests without voice enabled.

One old Box6 test references a deleted OCR log. As in the earlier validation,
surviving archived OCR for that document is substituted in memory for that test;
the missing file and old logs are not edited. Tk emits a ThemeChanged warning
during destruction of test windows; it does not fail assertions.

## Timing

Final saved-page measurements, seconds (small sequential sample, not a benchmark):

| Output | Version | OCR | Translation | Processing before audio | TTS stage | Total |
|---|---|---:|---:|---:|---:|---:|
| English | Box7 | 0.500 | 0.001 | 0.595 | 4.315 | 4.912 |
| English | Box8 OFF | 0.719 | 0.001 | 0.802 | 4.110 | 4.914 |
| English | Box8 ON | 0.765 | 0.001 | 0.862 | 4.082 | 4.948 |
| Urdu | Box7 | 0.875 | 2.144 | 3.094 | 5.025 | 8.121 |
| Urdu | Box8 OFF | 0.750 | 2.091 | 2.912 | 4.840 | 7.754 |
| Urdu | Box8 ON | 0.781 | 2.163 | 3.036 | 4.966 | 8.004 |

The extra models are not called in normal OCR or translation. A constant-time
reference to existing OCR fragments is retained; no extra OCR pass is performed.
Table workers run during actual PCM playback or after natural page completion,
and are suspended before the next speech request. They are cancelled on a new
capture/Stop. Q&A only runs on explicit request.

These figures show output parity, not guaranteed equal timing. In the English
sample, OCR was slower even with optional features OFF. Ordinary system/cache/GPU
variation and small sample size prevent causal speed claims. An earlier run also
showed higher TTS-stage time with background work; scheduling was tightened to
yield during synthesis. The final figures above use that tightened scheduling.

The test audio sink is silent and returns immediately: it validates generated
PCM and speech requests but does not simulate real-time headphone playback.
Therefore the real-process suspension test and scheduling unit test supplement
the full-pipeline checks. They do not replace a live test of CPU contention during
long Pi playback. No promise of literal zero CPU/RAM overhead is made.

Latest small model fixtures took about 2.2–4.1 s for Q&A and 2.9 s for table
detection/reconstruction with actual OCR fragments. Worker startup, translation
and speech playback add time to an explicit request; these values are not a
latency guarantee for real pages. The legacy TTS timer includes wall-clock time
spent on requested questions/table detours, not just Piper computation.

## Important behavior and limitations

- Source Q&A is extractive, in English, over the current page (translated to
  English on demand when needed). Answers are spoken in the selected English or
  Urdu output. It is not a general chat/summarization/reasoning model.
- Up to 24 lexically ranked paragraph/table-row candidates are passed to the
  question model. Windows cover long candidate text. Source links support
  checking; OCR, translation and answer selection can still be wrong.
- Clean ruled tables were verified. Rotated, merged, borderless, low-resolution
  and photographed/skewed tables are not guaranteed; uncertainty is shown and
  structured reading refused when reconstruction checks fail.
- Normal reading does not wait for table processing. If the table isn't ready
  when reading reaches it, normal OCR reading continues. Request “read the table”
  afterward. Mixed prose/table paragraphs keep their original reading.
- Existing paragraph/heading detours and new answers return to the saved
  paragraph during active reading. Exact-word restoration is not promised.
- No live microphone/audio/SSH/camera deployment test was performed for Box8.
  Existing Pi4 remains the client. Test your QCY voice, printed tables, actual
  source language, and disconnect/reconnect before presenting the demo.

## Evidence and continuation

`box8_validation/unit_checks.json`, `model_checks.json`, `speech_checks.json`,
`scheduling_checks.json`, `core_checks.json` and their logs retain the evidence.
`table_fixture.png` is the repeatable synthetic table input, not a photographed
wearable-camera accuracy benchmark. `BOX8_PROGRESS.md` contains the completed
checkpoint and protected hashes for future continuation.
