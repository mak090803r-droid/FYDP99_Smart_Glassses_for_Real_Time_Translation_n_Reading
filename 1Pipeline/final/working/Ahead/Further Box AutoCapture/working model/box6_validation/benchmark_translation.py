"""Replay stored OCR regions through exact Box5 functions and Box6 helper.

No OCR/model downloads/audio are run. Outputs are local diagnostic artifacts,
not translation accuracy measurements (these samples have no scored reference).
"""
import ast
import contextlib
import io
import json
import os
from pathlib import Path
import re
import sys
import time

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
sys.stdout.reconfigure(encoding="utf-8")
HERE = Path(__file__).resolve().parent
BASE = HERE.parent
PROJECT = next(parent for parent in BASE.parents
               if (parent / "pics").is_dir() and (parent / "1Pipeline").is_dir())
sys.path.insert(0, str(BASE))

import torch  # Initialize CUDA DLL paths before CTranslate2 on Windows.
import ctranslate2
import transformers
from box6_translation import translate_regions


def baseline_namespace():
    source = (BASE / "pipeline_cli_box5.py").read_text(encoding="utf-8-sig")
    tree = ast.parse(source)
    names = {"_split_translation_units", "_fit_translation_unit",
             "run_translation_nllb_paragraphs", "run_translation_nllb_paragraphs_for_output"}
    constants = {"NLLB_LANG_MAP", "PIPELINE_LANG_TO_NLLB", "BOX5_NLLB_LANG_MAP",
                 "BOX5_SOURCE_LANG_TO_NLLB", "BOX5_OUTPUT_LANG_TO_NLLB"}
    body = [node for node in tree.body if
            (isinstance(node, ast.FunctionDef) and node.name in names)
            or (isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id in constants for target in node.targets))]
    namespace = {"re": re, "time": time}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(BASE / "pipeline_cli_box5.py"), "exec"), namespace)
    return namespace


def parse_regions(text):
    sources = re.findall(r"^\s*SOURCE\s*:\s*(.+)$", text, re.MULTILINE)
    return [dict(region_id=index + 1, number=index + 1, source_text=source)
            for index, source in enumerate(sources)]


def log_sample(timestamp):
    path = PROJECT / "1Pipeline/final/working/ocr_results_comparison.txt"
    content = path.read_text(encoding="utf-8")
    for block in re.split(r"(?=TIMESTAMP\s*:)", content):
        if block.startswith(f"TIMESTAMP   : {timestamp}"):
            return parse_regions(block), str(path) + f" @ {timestamp}"
    raise RuntimeError(f"Saved run not found: {timestamp}")


def main():
    namespace = baseline_namespace()
    french, fr_evidence = log_sample("2026-07-23 14:31:13")
    chinese, zh_evidence = log_sample("2026-07-23 14:26:29")
    wrong_path = BASE / "paragraph_test_outputs/run_001_20260903_134627_ocr.txt"
    wrong = parse_regions(wrong_path.read_text(encoding="utf-8"))
    urdu_path = BASE / "paragraph_test_outputs/run_001_20260806_211116_ocr.txt"
    urdu_all = parse_regions(urdu_path.read_text(encoding="utf-8"))
    urdu = [urdu_all[index] for index in [0, 2, 4]]
    cases = [("saved_french", french, "french", "english", fr_evidence),
             ("saved_chinese", chinese, "chinese", "english", zh_evidence),
             ("english_with_chinese_ocr", wrong, "chinese", "english", str(wrong_path)),
             ("saved_english_to_urdu", urdu, "english", "urdu", str(urdu_path))]
    print("Loading local NLLB tokenizer/model", flush=True)
    tokenizer = transformers.AutoTokenizer.from_pretrained(
        "facebook/nllb-200-distilled-1.3B", local_files_only=True)
    model = ctranslate2.Translator(str(PROJECT / "nllb-200-1.3B-ct2"),
                                 device="cuda", compute_type="float16")
    tokenizer.src_lang = "fra_Latn"
    model.translate_batch([tokenizer.convert_ids_to_tokens(tokenizer.encode("Bonjour."))],
                          target_prefix=[["eng_Latn"]])
    report = {"ctranslate2_version": ctranslate2.__version__, "device": "cuda",
              "compute_type": "float16", "beam_size": 2,
              "notes": "Single warmed measurements; stored OCR text, not clean source. No BLEU/accuracy computed.",
              "cases": []}
    for name, regions, language, output, evidence in cases:
        print(f"Running {name} ({len(regions)} regions)", flush=True)
        item = {"name": name, "source_language": language, "target_language": output,
                "evidence": evidence, "region_count": len(regions)}
        for version, function in [("box5", namespace["run_translation_nllb_paragraphs_for_output"]),
                                  ("box6", translate_regions)]:
            console = io.StringIO()
            started = time.perf_counter()
            with contextlib.redirect_stdout(console):
                result, elapsed = function(regions, language, output, model, tokenizer)
            item[version] = {"elapsed_seconds": time.perf_counter() - started,
                             "reported_stage_seconds": elapsed, "regions": result,
                             "console": console.getvalue()}
            print(f"  {version}: {item[version]['elapsed_seconds']:.3f}s", flush=True)
        report["cases"].append(item)
        (HERE / "translation_benchmark.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    del model
    print(f"GPU released. Report: {HERE / 'translation_benchmark.json'}", flush=True)


if __name__ == "__main__":
    main()
