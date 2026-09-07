r"""
OCR accuracy evaluator for the Smart Glasses FYDP pipeline.

The script compares the RAW EXTRACTED TEXT in a pipeline ``*_ocr.txt`` log
against a ground-truth ``.txt`` or ``.docx`` file. It calculates:

    - Character accuracy
    - Character error rate (CER)
    - Word accuracy
    - Word error rate (WER)
    - Mean OCR region confidence

A formal PNG results table is generated for use in a PowerPoint presentation.

Example:
    C:\Users\ali\Desktop\FYDP\fydp\Scripts\python.exe accuracy_check.py ^
        --ocr-log "paragraph_test_outputs\run_010_20260723_151236_ocr.txt" ^
        --ground-truth "paragraph_test_outputs\ground truth\English_OCR_Test_2x16pt_2x18pt.docx"
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import unicodedata
import zipfile
from pathlib import Path
from typing import Iterable, Sequence
from xml.etree import ElementTree

from PIL import Image, ImageDraw, ImageFont


WORD_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
WORD_TAG = f"{{{WORD_NAMESPACE}}}"


def read_text_file(path: Path) -> str:
    """Read a text file while tolerating common Windows encodings."""
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError(
        "unknown", b"", 0, 1, f"Could not decode text file: {path}")


def extract_pipeline_ocr_text(log_text: str) -> str:
    """Extract only the raw OCR result from a pipeline debug log."""
    lines = log_text.splitlines()
    start_index = None
    end_index = None

    for index, line in enumerate(lines):
        upper = line.upper()
        if "RAW EXTRACTED TEXT" in upper:
            start_index = index + 1
            continue
        if start_index is not None and (
                "CLASSIFIED REGION DETAIL" in upper
                or "REGION-BY-REGION" in upper
                or "REGION TRANSLATION" in upper):
            end_index = index
            break

    if start_index is not None:
        selected = lines[start_index:end_index]
        text = " ".join(line.strip() for line in selected if line.strip())
        if text:
            return text

    # Compatibility with simpler OCR result logs.
    labelled_patterns = (
        r"(?is)\bRAW OCR TEXT\s*:\s*(.+?)(?:\n\s*\n|$)",
        r"(?is)\bFULL OCR TEXT\s*:\s*(.+?)(?:\n\s*\n|$)",
        r"(?is)\bEXTRACTED TEXT\s*:\s*(.+?)(?:\n\s*\n|$)",
    )
    for pattern in labelled_patterns:
        match = re.search(pattern, log_text)
        if match:
            return match.group(1).strip()

    # A plain text file may itself be the OCR output.
    if not re.search(r"(?im)^\s*(?:RUN|TIMESTAMP|IMAGE FILE)\s*:", log_text):
        return log_text.strip()

    raise ValueError(
        "Could not find 'RAW EXTRACTED TEXT' in the OCR log.")


def extract_region_confidences(log_text: str) -> list[float]:
    """Extract one confidence value per title/heading/paragraph region."""
    values: list[float] = []
    patterns = (
        re.compile(
            r"(?im)^\s*(?:TITLE|H\d+|P\d+)\s*\|[^\n]*?"
            r"\bconf\s*=\s*([01](?:\.\d+)?)"),
        re.compile(
            r"(?im)^\s*REGION\s+\S+\s*\|[^\n]*?"
            r"\bCONFIDENCE\s*=\s*([01](?:\.\d+)?)"),
    )
    for pattern in patterns:
        for match in pattern.finditer(log_text):
            value = float(match.group(1))
            if 0.0 <= value <= 1.0:
                values.append(value)
    return values


def extract_docx_pages(path: Path) -> list[str]:
    """
    Extract text from a DOCX and retain explicit Word page-break boundaries.

    The supplied FYDP ground-truth documents contain multiple printed test
    pages. Keeping those pages separate allows automatic selection of the page
    that corresponds to the OCR log.
    """
    with zipfile.ZipFile(path) as archive:
        document_xml = archive.read("word/document.xml")

    root = ElementTree.fromstring(document_xml)
    pages: list[list[str]] = [[]]

    for paragraph in root.iter(f"{WORD_TAG}p"):
        paragraph_parts: list[str] = []
        for node in paragraph.iter():
            if node.tag == f"{WORD_TAG}t" and node.text:
                paragraph_parts.append(node.text)
            elif node.tag == f"{WORD_TAG}tab":
                paragraph_parts.append(" ")
            elif (
                    node.tag == f"{WORD_TAG}lastRenderedPageBreak"
                    or (
                        node.tag == f"{WORD_TAG}br"
                        and node.attrib.get(f"{WORD_TAG}type") == "page")):
                before_break = "".join(paragraph_parts).strip()
                if before_break:
                    pages[-1].append(before_break)
                paragraph_parts = []
                if pages[-1]:
                    pages.append([])

        remaining = "".join(paragraph_parts).strip()
        if remaining:
            pages[-1].append(remaining)

    extracted = [
        "\n".join(paragraphs).strip()
        for paragraphs in pages
        if any(paragraph.strip() for paragraph in paragraphs)
    ]
    if not extracted:
        raise ValueError(f"No text was found in DOCX: {path}")
    return extracted


def load_ground_truth_candidates(path: Path) -> list[str]:
    """Return one candidate for TXT, or separate page candidates for DOCX."""
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return extract_docx_pages(path)
    if suffix in {".txt", ".md"}:
        return [read_text_file(path)]
    raise ValueError(
        "Ground truth must be a .txt, .md, or .docx file.")


def normalize_text(
        text: str,
        *,
        ignore_case: bool = False,
        ignore_punctuation: bool = False) -> str:
    """Normalize typography and whitespace without hiding OCR substitutions."""
    text = unicodedata.normalize("NFKC", text)
    typography_map = str.maketrans({
        "\u2018": "'", "\u2019": "'", "\u201a": "'",
        "\u201c": '"', "\u201d": '"',
        "\u2013": "-", "\u2014": "-",
        "\u00a0": " ",
    })
    text = text.translate(typography_map)
    if ignore_punctuation:
        text = "".join(
            character for character in text
            if not unicodedata.category(character).startswith("P"))
    text = re.sub(r"\s+", " ", text).strip()
    if ignore_case:
        text = text.casefold()
    return text


def tokenize_words(text: str, ignore_case: bool = False) -> list[str]:
    """Create Unicode word tokens while retaining apostrophes and hyphens."""
    text = normalize_text(
        text, ignore_case=ignore_case, ignore_punctuation=False)
    return re.findall(r"[^\W_]+(?:['-][^\W_]+)*|\d+(?:[.,]\d+)*", text)


def levenshtein_distance(
        reference: Sequence[str] | str,
        hypothesis: Sequence[str] | str) -> int:
    """Memory-efficient Levenshtein edit distance."""
    if len(reference) < len(hypothesis):
        reference, hypothesis = hypothesis, reference
    previous = list(range(len(hypothesis) + 1))
    for ref_index, ref_item in enumerate(reference, start=1):
        current = [ref_index]
        for hyp_index, hyp_item in enumerate(hypothesis, start=1):
            insertion = current[hyp_index - 1] + 1
            deletion = previous[hyp_index] + 1
            substitution = (
                previous[hyp_index - 1] +
                (0 if ref_item == hyp_item else 1))
            current.append(min(insertion, deletion, substitution))
        previous = current
    return previous[-1]


def calculate_metrics(
        ground_truth: str,
        ocr_text: str,
        confidences: Iterable[float],
        *,
        ignore_case: bool = False,
        ignore_punctuation: bool = False) -> dict[str, float | int | None]:
    """Calculate CER, WER, their complementary accuracies, and confidence."""
    reference_chars = normalize_text(
        ground_truth,
        ignore_case=ignore_case,
        ignore_punctuation=ignore_punctuation)
    hypothesis_chars = normalize_text(
        ocr_text,
        ignore_case=ignore_case,
        ignore_punctuation=ignore_punctuation)

    if not reference_chars:
        raise ValueError("Ground-truth text is empty after normalization.")

    character_edits = levenshtein_distance(
        reference_chars, hypothesis_chars)
    cer = character_edits / len(reference_chars)

    reference_words = tokenize_words(ground_truth, ignore_case=ignore_case)
    hypothesis_words = tokenize_words(ocr_text, ignore_case=ignore_case)
    if not reference_words:
        raise ValueError("Ground truth contains no measurable words.")

    word_edits = levenshtein_distance(reference_words, hypothesis_words)
    wer = word_edits / len(reference_words)
    confidence_values = list(confidences)

    return {
        "character_accuracy": max(0.0, 1.0 - cer) * 100.0,
        "character_error_rate": cer * 100.0,
        "word_accuracy": max(0.0, 1.0 - wer) * 100.0,
        "word_error_rate": wer * 100.0,
        "mean_region_confidence": (
            statistics.fmean(confidence_values) * 100.0
            if confidence_values else None),
        "character_edits": character_edits,
        "reference_characters": len(reference_chars),
        "ocr_characters": len(hypothesis_chars),
        "word_edits": word_edits,
        "reference_words": len(reference_words),
        "ocr_words": len(hypothesis_words),
        "region_count": len(confidence_values),
    }


def choose_ground_truth(
        candidates: list[str],
        ocr_text: str,
        requested_page: int | None,
        *,
        ignore_case: bool,
        ignore_punctuation: bool) -> tuple[str, int]:
    """Select a requested DOCX page, or automatically choose the closest one."""
    if requested_page is not None:
        if requested_page < 1 or requested_page > len(candidates):
            raise ValueError(
                f"Ground-truth page must be between 1 and {len(candidates)}.")
        return candidates[requested_page - 1], requested_page

    hypothesis = normalize_text(
        ocr_text,
        ignore_case=ignore_case,
        ignore_punctuation=ignore_punctuation)
    best_page = 1
    best_rate = float("inf")
    for index, candidate in enumerate(candidates, start=1):
        reference = normalize_text(
            candidate,
            ignore_case=ignore_case,
            ignore_punctuation=ignore_punctuation)
        if not reference:
            continue
        rate = levenshtein_distance(reference, hypothesis) / len(reference)
        if rate < best_rate:
            best_rate = rate
            best_page = index
    return candidates[best_page - 1], best_page


def find_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Load a professional Windows font with a portable Pillow fallback."""
    windows_fonts = Path("C:/Windows/Fonts")
    names = (
        ("segoeuib.ttf", "arialbd.ttf", "calibrib.ttf")
        if bold else
        ("segoeui.ttf", "arial.ttf", "calibri.ttf")
    )
    for name in names:
        path = windows_fonts / name
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default(size=size)


def centered_text(
        draw: ImageDraw.ImageDraw,
        box: tuple[int, int, int, int],
        text: str,
        font: ImageFont.ImageFont,
        fill: tuple[int, int, int]) -> None:
    """Draw one line centered vertically and horizontally in a table cell."""
    left, top, right, bottom = box
    bounds = draw.textbbox((0, 0), text, font=font)
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]
    x = left + (right - left - width) / 2
    y = top + (bottom - top - height) / 2 - bounds[1]
    draw.text((x, y), text, font=font, fill=fill)


def create_results_image(
        metrics: dict[str, float | int | None],
        output_path: Path,
        title: str,
        subtitle: str) -> None:
    """Create a formal, high-resolution results table for a presentation."""
    width, height = 1400, 940
    image = Image.new("RGB", (width, height), (248, 250, 253))
    draw = ImageDraw.Draw(image)

    navy = (23, 62, 103)
    dark = (28, 40, 56)
    grid = (188, 204, 220)
    white = (255, 255, 255)
    alternate = (242, 247, 252)

    title_font = find_font(52, bold=True)
    subtitle_font = find_font(23)
    header_font = find_font(34, bold=True)
    label_font = find_font(32)
    value_font = find_font(36)
    footer_font = find_font(20)

    draw.text((90, 48), title, font=title_font, fill=navy)
    draw.text((92, 119), subtitle, font=subtitle_font, fill=(76, 92, 110))

    table_left, table_right = 90, 1310
    table_top = 185
    header_height = 92
    row_height = 118
    divider = 835

    draw.rectangle(
        (table_left, table_top, table_right, table_top + header_height),
        fill=navy)
    draw.text(
        (table_left + 28, table_top + 23),
        "Measure", font=header_font, fill=white)
    centered_text(
        draw,
        (divider, table_top, table_right, table_top + header_height),
        "Result", header_font, white)

    mean_confidence = metrics["mean_region_confidence"]
    rows = [
        ("Character accuracy", metrics["character_accuracy"]),
        ("Character error rate (CER)", metrics["character_error_rate"]),
        ("Word accuracy", metrics["word_accuracy"]),
        ("Word error rate (WER)", metrics["word_error_rate"]),
        ("Mean region confidence", mean_confidence),
    ]

    for index, (label, value) in enumerate(rows):
        top = table_top + header_height + index * row_height
        bottom = top + row_height
        fill = white if index % 2 == 0 else alternate
        draw.rectangle(
            (table_left, top, table_right, bottom),
            fill=fill, outline=grid, width=2)
        draw.line((divider, top, divider, bottom), fill=grid, width=2)
        draw.text(
            (table_left + 28, top + 35),
            label, font=label_font, fill=dark)
        result = "N/A" if value is None else f"{float(value):.2f}%"
        centered_text(
            draw, (divider, top, table_right, bottom),
            result, value_font, dark)

    draw.line(
        (divider, table_top, divider, table_top + header_height),
        fill=white, width=2)
    footer_y = table_top + header_height + len(rows) * row_height + 28
    footer = (
        f"Levenshtein evaluation | "
        f"{metrics['reference_characters']} reference characters | "
        f"{metrics['reference_words']} reference words | "
        f"{metrics['region_count']} OCR regions")
    draw.text((92, footer_y), footer, font=footer_font, fill=(86, 101, 118))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, format="PNG", optimize=True)


def print_results(
        metrics: dict[str, float | int | None],
        selected_page: int,
        page_count: int,
        output_path: Path) -> None:
    """Print a screenshot-friendly console report."""
    mean_confidence = metrics["mean_region_confidence"]
    rows = [
        ("Character accuracy", metrics["character_accuracy"]),
        ("Character error rate (CER)", metrics["character_error_rate"]),
        ("Word accuracy", metrics["word_accuracy"]),
        ("Word error rate (WER)", metrics["word_error_rate"]),
        ("Mean region confidence", mean_confidence),
    ]

    print("\n" + "=" * 62)
    print("                 OCR ACCURACY CHECK")
    print("=" * 62)
    print(f"{'Measure':<40}{'Result':>20}")
    print("-" * 62)
    for label, value in rows:
        result = "N/A" if value is None else f"{float(value):.2f}%"
        print(f"{label:<40}{result:>20}")
    print("-" * 62)
    print(
        f"Character edits: {metrics['character_edits']} / "
        f"{metrics['reference_characters']}")
    print(
        f"Word edits     : {metrics['word_edits']} / "
        f"{metrics['reference_words']}")
    if page_count > 1:
        print(f"Ground truth   : page {selected_page} of {page_count}")
    print(f"PPT image      : {output_path}")
    print("=" * 62 + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Calculate OCR CER, WER, accuracies, and mean region confidence "
            "from a Smart Glasses pipeline OCR log."))
    parser.add_argument(
        "--ocr-log", type=Path,
        help=(
            "Pipeline *_ocr.txt log containing RAW EXTRACTED TEXT. "
            "If omitted, the newest pipeline OCR log is used."))
    parser.add_argument(
        "--ground-truth", type=Path,
        help=(
            "Correct reference text as .txt, .md, or .docx. If omitted, "
            "the matching project ground-truth document is selected."))
    parser.add_argument(
        "--ground-truth-page", type=int,
        help=(
            "1-based DOCX page to compare. By default the closest page is "
            "selected automatically."))
    parser.add_argument(
        "--output", type=Path,
        help=(
            "PPT-ready PNG output path. Defaults to "
            "accuracy_check_results.png beside this script."))
    parser.add_argument(
        "--json-output", type=Path,
        help="Optional JSON metrics output path.")
    parser.add_argument(
        "--ignore-case", action="store_true",
        help="Do not count uppercase/lowercase differences as OCR errors.")
    parser.add_argument(
        "--ignore-punctuation", action="store_true",
        help="Do not count punctuation differences in the character metric.")
    parser.add_argument(
        "--title", default="OCR Accuracy Evaluation",
        help="Title printed above the PNG table.")
    return parser


def find_automatic_inputs(script_dir: Path) -> tuple[Path, Path]:
    """Find the newest pipeline log and its most likely ground-truth document."""
    output_dir = script_dir / "paragraph_test_outputs"
    logs = sorted(
        output_dir.glob("run_*_ocr.txt"),
        key=lambda candidate: candidate.stat().st_mtime,
        reverse=True)
    if not logs:
        raise FileNotFoundError(
            f"No pipeline *_ocr.txt logs were found in: {output_dir}")

    ocr_log = logs[0]
    log_text = read_text_file(ocr_log)
    language_match = re.search(
        r"(?im)^\s*LANGUAGE\s*:\s*([A-Za-z]+)", log_text)
    language = (
        language_match.group(1).strip().lower()
        if language_match else "english")

    ground_truth_dir = output_dir / "ground truth"
    if language == "english":
        preferred_names = (
            "English_OCR_Test_2x16pt_2x18pt.docx",
        )
    else:
        preferred_names = (
            "Multilingual_Translation_Test_18pt.docx",
            "Multilingual_Translation_Test_16pt.docx",
        )

    for name in preferred_names:
        candidate = ground_truth_dir / name
        if candidate.is_file():
            return ocr_log, candidate

    available = sorted(ground_truth_dir.glob("*.docx"))
    if available:
        return ocr_log, available[0]
    raise FileNotFoundError(
        f"No ground-truth DOCX files were found in: {ground_truth_dir}")


def main() -> int:
    args = build_parser().parse_args()
    script_dir = Path(__file__).resolve().parent
    automatic_log = automatic_ground_truth = None
    if args.ocr_log is None or args.ground_truth is None:
        automatic_log, automatic_ground_truth = find_automatic_inputs(
            script_dir)
    ocr_log_path = (
        args.ocr_log.resolve()
        if args.ocr_log else automatic_log.resolve())
    ground_truth_path = (
        args.ground_truth.resolve()
        if args.ground_truth else automatic_ground_truth.resolve())
    output_path = (
        args.output.resolve()
        if args.output else (script_dir / "accuracy_check_results.png"))

    if args.ocr_log is None:
        print(f"[AUTO] Using newest OCR log: {ocr_log_path.name}")
    if args.ground_truth is None:
        print(f"[AUTO] Using ground truth: {ground_truth_path.name}")

    if not ocr_log_path.is_file():
        raise FileNotFoundError(f"OCR log not found: {ocr_log_path}")
    if not ground_truth_path.is_file():
        raise FileNotFoundError(
            f"Ground-truth file not found: {ground_truth_path}")

    log_text = read_text_file(ocr_log_path)
    ocr_text = extract_pipeline_ocr_text(log_text)
    confidences = extract_region_confidences(log_text)
    candidates = load_ground_truth_candidates(ground_truth_path)
    ground_truth, selected_page = choose_ground_truth(
        candidates,
        ocr_text,
        args.ground_truth_page,
        ignore_case=args.ignore_case,
        ignore_punctuation=args.ignore_punctuation)
    metrics = calculate_metrics(
        ground_truth,
        ocr_text,
        confidences,
        ignore_case=args.ignore_case,
        ignore_punctuation=args.ignore_punctuation)

    subtitle = (
        f"{ocr_log_path.stem}  |  "
        f"Ground truth: {ground_truth_path.name}")
    create_results_image(metrics, output_path, args.title, subtitle)
    print_results(metrics, selected_page, len(candidates), output_path)

    if args.json_output:
        json_path = args.json_output.resolve()
        json_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "ocr_log": str(ocr_log_path),
            "ground_truth": str(ground_truth_path),
            "selected_ground_truth_page": selected_page,
            "ground_truth_page_count": len(candidates),
            "normalization": {
                "ignore_case": args.ignore_case,
                "ignore_punctuation": args.ignore_punctuation,
            },
            **metrics,
        }
        json_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8")
        print(f"JSON report    : {json_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
