# Box8: table reading and source-linked questions

Box8 is a separate host copy. Box7 and all its helper files, Box6 and Pi4 are
unchanged. Keep using `piweb_cli4.py` on the Pi: no Pi5, new Pi files, or new
network ports are required for these features.

## Start

Close the old host first so it releases the camera/audio/voice ports. On the PC:

```powershell
& 'C:\Users\ali\Desktop\FYDP\fydp\Scripts\python.exe' 'C:\Users\ali\Desktop\FYDP\New\Project\1Pipeline\final\working\Ahead\Further Box AutoCapture\working model\pipeline_cli_box8.py'
```

Initialize/connect and use the original language, Book Mode, speed and Audio(Pi)
controls as before. Open **Page tools** and enable either or both:

- **Table-aware reading**
- **Source-linked questions**

Both start OFF. They are independent. Typed questions and the Page tools buttons
work without microphone/voice input enabled. For spoken requests, also enable
Voice Input, select Pi earbuds/default, and use the existing G or Capture hold.
Keep QCY in its working Headset/HFP profile. Nothing here changes Pi audio setup.

## Source-linked Q&A

Capture a page and ask in English, for example:

- “What voltage does the camera require?”
- “What should I do before cleaning?”
- “Read the source paragraph.”

Answers are spoken through the selected existing English/Urdu Piper output,
displayed in Page tools, and linked to the supporting paragraph box. For a
non-English page, the existing English translation is reused when available;
otherwise an English version is produced on demand with the existing NLLB model.
That optional translation time belongs to the question operation, not normal
page translation. Urdu answers use NLLB on demand and the already loaded Urdu TTS.

The local model extracts a source span; this is NOT a general conversational LLM.
It supports factual questions that can be answered from the current captured
page. It does not promise broad summaries, multi-step reasoning or answers from
previous pages. Low-support questions receive a spoken “could not find” response.
The shown model score is not a calibrated probability of correctness. Source
linking helps verification but cannot eliminate OCR/translation/model mistakes.

“Read the source paragraph” reads the full supporting paragraph using the
document's selected output language. After an answer during active reading,
playback automatically returns to the saved paragraph and continues. Return is
paragraph-level, not the exact interrupted word. Requests after natural completion
do not automatically restart the entire page. Stop remains an explicit stop.

## Table-aware reading

The table models detect the table and reconstruct rows/columns. A complete ruled
grid, when present, refines cell boundaries; borderless tables use the learned
structure. Raw OCR fragments from the existing OCR result are assigned to cells;
normal paragraph grouping/text is not edited and no extra OCR pass is required.

Once ready, Page tools shows the headers and cell text. During normal playback,
ready reliable tables replace fully-contained table paragraphs with structured
row reading. Non-table text is retained. Normal paragraph IDs do not change.

Commands:

| Say | Action |
|---|---|
| Read the table | Read the selected table, initially table 1 |
| Read table 2 | Select and read table 2 |
| Read/repeat row 2 | Read one data row with its column labels |
| Next row / previous row / repeat row | Navigate within the selected table |
| Read column 2 | Read one numbered column |
| Read the voltage column | Read a column with that exact header name |
| Skip this table | Skip its structured reading and return to the page |

Rows/cells being read are outlined in the captured image preview. Multiple header
rows, merged/spanning cells, incomplete structures, crossing OCR fragments and
low-confidence cell text are flagged as CHECK REQUIRED. Box8 does not guess how
to split a text fragment across cells. Rotated tables request an upright recapture.
Uncertain tables keep the normal OCR reading available and refuse structured
table narration. Borderless, skewed and complex scientific tables need more
real-document validation than the included clean ruled-table test.

For a supported numerical question such as “Which component uses the most
current?”, complete numeric columns with matching units are compared by code,
not guessed by the language model. Ambiguous headers, missing values and mixed
units are refused in this version; no unit conversion is assumed.

## Timing and scheduling

- OFF: no optional model loading or inference. The raw OCR metadata reference is
  retained with the captured page, enabling later activation without re-OCR.
- Table ON: work starts only after normal OCR/translation, during actual PCM
  playback or after natural page completion. The table worker is suspended before
  the next Piper speech request and resumed in an available playback window.
  This keeps table inference from deliberately overlapping paragraph synthesis.
  It uses two CPU threads and below-normal Windows priority, not the OCR/NLLB GPU.
- Q&A: model work starts only when a question is asked. Ordinary reading is
  interrupted for that explicit operation, then resumed. Workers exit after each
  request to free memory. Subsequent questions still include model-load overhead.
- A new capture, Stop or closing Box8 cancels optional jobs. Results from a
  cancelled/previous page are discarded.

There is no compulsory table-model wait before normal page audio. If a result
arrives after the relevant paragraph has started, that paragraph is not rewound.
Use **Read table** afterward; an explicit request waits for table processing if
necessary. Mixed paragraphs containing both prose and table content also retain
normal reading, with structured reading available on request.

Additional models necessarily use RAM/CPU and can contend with Piper/system work.
Process suspension/resumption and normal scheduling still have small overhead;
zero timing impact cannot be guaranteed on one shared PC. Existing core model
settings and algorithms are preserved; see BOX8_REPORT.md for measured results.
The historical TTS playback timer is wall time and includes requested detours or
waiting during reading; that is not a measurement of Piper inference alone.

## Local assets and provenance

Already provisioned on this PC under `box8_models`:

- [Microsoft Table Transformer detection](https://huggingface.co/microsoft/table-transformer-detection)
- [Microsoft Table Transformer structure recognition](https://huggingface.co/microsoft/table-transformer-structure-recognition)
- [deepset RoBERTa-base SQuAD2 extractive QA](https://huggingface.co/deepset/roberta-base-squad2)

The downloaded folders contain approximately 1.36 GiB including both provided
weight formats. The runtime loads one weight format, not both. `timm==1.0.22` is
isolated in `.box8_deps`; existing Python packages were not upgraded. Table models
load their local backbone weights without requesting an online backbone download.

Runtime is offline and fails explicitly if assets are absent. Only this explicit
setup sequence downloads files; run from the working-model directory if moving
the project to another PC with a compatible fydp environment:

```powershell
& 'C:\Users\ali\Desktop\FYDP\fydp\Scripts\python.exe' -m pip install --no-deps --target .box8_deps timm==1.0.22
& 'C:\Users\ali\Desktop\FYDP\fydp\Scripts\python.exe' setup_box8_models.py
```

Model licenses/model cards remain at the linked sources; review those before
redistributing weights.

## Tests and continuation

From the working-model directory:

```powershell
& 'C:\Users\ali\Desktop\FYDP\fydp\Scripts\python.exe' box8_validation\run_checks.py
& 'C:\Users\ali\Desktop\FYDP\fydp\Scripts\python.exe' box8_validation\model_checks.py
& 'C:\Users\ali\Desktop\FYDP\fydp\Scripts\python.exe' box8_validation\speech_checks.py
& 'C:\Users\ali\Desktop\FYDP\fydp\Scripts\python.exe' box8_validation\core_checks.py
```

Generated test evidence stays under `box8_validation`. Old source files and test
logs are not overwritten. BOX8_PROGRESS.md records implementation state for later
continuation. The new features still need a live QCY/Pi test with your own printed
tables, accent and lighting; synthetic speech/silent output is not hardware proof.
