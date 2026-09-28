"""Compare Box5/Box6 OCR on matched project captures without live side effects."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
from xml.etree import ElementTree
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--language", default="chinese")
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).with_name("ocr_regression.json"))
    args = parser.parse_args()
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    baseline = load(ROOT / "pipeline_cli_box5.py", "box5_ocr_regression")
    candidate = load(ROOT / "pipeline_cli_box6.py", "box6_ocr_regression")
    ocr = baseline.load_ocr_engine(args.language)
    truth_path = Path(baseline.PROJECT_DIR) / "1Pipeline/final/working/ground_truth/English_OCR_Test_2x16pt_2x18pt.docx"
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    with ZipFile(truth_path) as archive:
        xml = ElementTree.fromstring(archive.read("word/document.xml"))
    paragraphs = ["".join(node.text or "" for node in paragraph.findall(".//w:t", namespace))
                  for paragraph in xml.findall(".//w:p", namespace)]
    nonempty = [text for text in paragraphs if text.strip()]
    reference = " ".join(nonempty[:4])
    paths = [
        Path(baseline.CAPTURED_DIR) / "capture_cli_001_20260903_134624.jpg",
        ROOT / "paragraph_test_outputs/live_debug_sessions/session_20260806_210058/raw_analysis_frames/analysis_00070.jpg",
    ]
    records = []
    for image_path in paths:
        outputs = {}
        for name, module in (("box5", baseline), ("box6", candidate)):
            text, regions, _, seconds = module.run_ocr(ocr, str(image_path), False, None)
            metrics = baseline.calculate_text_metrics(reference, text)
            outputs[name] = {
                "text": text, "region_count": len(regions), "seconds": seconds,
                "region_types": [region.get("region_type") for region in regions],
                "region_line_counts": [len(region["lines"]) for region in regions],
                "metrics": metrics,
            }
        record = {"image": str(image_path), "outputs": outputs,
                  "text_identical": outputs["box5"]["text"] == outputs["box6"]["text"]}
        records.append(record)
        print(json.dumps(record, ensure_ascii=True), flush=True)
    report = {"recognition_language_setting": args.language,
              "document_language": "english", "reference_document": str(truth_path),
              "reference": reference, "tests": records,
              "note": "Matches first title and three body paragraphs in the human source document. Chinese OCR on English is intentional to reproduce the saved session."}
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
