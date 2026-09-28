"""Local NLLB translation with bounded context and stable OCR region identity.

This module deliberately has no model-loading side effects. The host supplies
its already loaded CTranslate2 translator and tokenizer.
"""

from collections import Counter
import re
import time
import unicodedata


LANGUAGE_TOKENS = {
    "english": "eng_Latn", "french": "fra_Latn",
    "spanish": "spa_Latn", "chinese": "zho_Hans", "urdu": "urd_Arab",
}
MAX_SOURCE_TOKENS = 400
MAX_DECODING_TOKENS = 512
MAX_BATCH_SIZE = 8
BEAM_SIZE = 2  # CTranslate2 4.8 default used by Box5, now explicit.

_ENGLISH_EVIDENCE = set(
    "the with which this that from should without between after before when "
    "where while would could have has were been are into through their there "
    "these those will than then each also can every only because remains "
    "allows image text document page system camera".split())
_ROMANCE_EVIDENCE = set(
    "les des une est pour dans sur avec sont qui que nous vous aux mais "
    "ainsi chaque cette ces leur leurs sans entre el los las una del para "
    "con por como pero tiene son sus más cada cuando donde puede".split())
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
_NUMBER = re.compile(r"(?<!\w)\d+(?:[.,]\d+)*(?!\w)")
_IDENTIFIER = re.compile(
    r"(?<!\w)(?:[A-Z][A-Z0-9]{1,11}|"
    r"[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)+|"
    r"[A-Za-z]+\d+[A-Za-z0-9]*)(?!\w)")
_STANDALONE_ACRONYMS = {
    "UAS", "UAV", "OCR", "TTS", "NLLB", "GPU", "CPU", "USB",
    "ISO", "IEC", "AC", "DC", "TCP", "UDP", "IP", "HTTP", "HTTPS",
}
_STANDALONE_TECHNICAL_ATOMS = {"Li-ion", "Li-on", "Li-Ion", "LiPo"}


class TranslationError(RuntimeError):
    """A translation could not be completed without silent content loss."""


def _script_counts(text):
    counts = Counter()
    for char in text:
        if not char.isalpha():
            continue
        name = unicodedata.name(char, "")
        if "CJK" in name or "IDEOGRAPH" in name:
            counts["han"] += 1
        elif "HANGUL" in name:
            counts["hangul"] += 1
        elif "ARABIC" in name:
            counts["arabic"] += 1
        elif "LATIN" in name:
            counts["latin"] += 1
        counts["letters"] += 1
    return counts


def resolve_source_language(text, selected_language):
    """Separate the user's OCR model choice from strong translation evidence.

    Only one correction is automatic: Chinese-selected OCR on a long, clearly
    English document. Ambiguous Latin text never switches French/Spanish.
    Returns (effective language, diagnostic messages).
    """
    if selected_language not in LANGUAGE_TOKENS:
        raise ValueError(f"Unsupported source language: {selected_language}")
    scripts = _script_counts(text)
    total = max(1, scripts["letters"])
    words = [word.lower() for word in _WORD.findall(text)]
    english = [word for word in words if word in _ENGLISH_EVIDENCE]
    romance = [word for word in words if word in _ROMANCE_EVIDENCE]
    warnings = []
    if (selected_language == "chinese" and len(words) >= 50
            and scripts["latin"] / total >= 0.98
            and len(set(english)) >= 8
            and len(english) / max(1, len(words)) >= 0.15
            and len(romance) / max(1, len(words)) < 0.06):
        warnings.append(
            "OCR remains Chinese-selected; the document has strong English "
            "language evidence, so NLLB source is English.")
        return "english", warnings
    if (selected_language == "chinese" and total >= 30
            and scripts["latin"] / total >= 0.90):
        warnings.append(
            "Selected Chinese source contains predominantly Latin text; "
            "language is uncertain. Verify the source selection.")
    if (selected_language != "chinese" and scripts["han"] >= 8
            and scripts["han"] / total >= 0.35):
        warnings.append(
            "Detected substantial Chinese script but a different source "
            "language is selected. Verify the source selection.")
    if scripts["hangul"] >= 8:
        warnings.append("Korean script detected; Korean source is not configured.")
    if selected_language != "urdu" and scripts["arabic"] >= 8:
        warnings.append(
            "Arabic-script text detected; this does not uniquely identify "
            "Arabic or Urdu. Verify the source selection.")
    return selected_language, warnings


def is_nonlinguistic_region(text):
    """Preserve standalone page numbers and short machine identifiers."""
    stripped = text.strip()
    if not stripped or not any(char.isalpha() for char in stripped):
        return True
    tokens = stripped.split()
    if not 1 <= len(tokens) <= 3:
        return False
    for token in tokens:
        atom = token.strip("()[]{}:;,.")
        if not any(char.isalpha() for char in atom):
            continue
        if atom in _STANDALONE_ACRONYMS or atom in _STANDALONE_TECHNICAL_ATOMS:
            continue
        if (_IDENTIFIER.fullmatch(atom) is not None
                and any(char.isdigit() for char in atom)):
            continue
        # Uppercase alone does not establish an acronym: headings such as
        # STOP THE ENGINE still require translation.
        return False
    return True


def _encoded(tokenizer, text):
    return tokenizer.encode(text, truncation=False)


def _split_oversize_atom(atom, tokenizer, max_tokens):
    """Split a no-space token safely, including URLs and long CJK text."""
    pieces = []
    while atom:
        if len(_encoded(tokenizer, atom)) <= max_tokens:
            pieces.append(atom)
            break
        low, high, best = 1, len(atom), 0
        while low <= high:
            midpoint = (low + high) // 2
            if len(_encoded(tokenizer, atom[:midpoint])) <= max_tokens:
                best = midpoint
                low = midpoint + 1
            else:
                high = midpoint - 1
        if not best:
            raise TranslationError("Source token budget is too small for one character.")
        pieces.append(atom[:best])
        atom = atom[best:]
    return pieces


def _fit_unit(unit, tokenizer, max_tokens):
    if len(_encoded(tokenizer, unit)) <= max_tokens:
        return [unit]
    parts, current = [], ""
    for atom in re.findall(r"\S+\s*", unit):
        if len(_encoded(tokenizer, current + atom)) <= max_tokens:
            current += atom
            continue
        if current.strip():
            parts.append(current.strip())
        atom_parts = _split_oversize_atom(atom, tokenizer, max_tokens)
        parts.extend(part.strip() for part in atom_parts[:-1] if part.strip())
        current = atom_parts[-1] if atom_parts else ""
    if current.strip():
        parts.append(current.strip())
    return parts


def chunk_paragraph(text, tokenizer, max_tokens=MAX_SOURCE_TOKENS):
    """Pack adjacent sentences together while guaranteeing every token fits.

    Only oversized paragraphs are divided. Splits retain punctuation and all
    non-whitespace source characters, including text without word boundaries.
    """
    text = text.strip()
    if not text:
        return []
    if len(_encoded(tokenizer, text)) <= max_tokens:
        return [text]
    sentence_units = re.split(r"(?<=[。！？؟۔])\s*|(?<=[.!?])\s+", text)
    chunks, current = [], ""
    for unit in sentence_units:
        if not unit.strip():
            continue
        for fitted in _fit_unit(unit.strip(), tokenizer, max_tokens):
            candidate = f"{current} {fitted}".strip()
            if current and len(_encoded(tokenizer, candidate)) > max_tokens:
                chunks.append(current)
                current = fitted
            else:
                current = candidate
    if current:
        chunks.append(current)
    if any(len(_encoded(tokenizer, chunk)) > max_tokens for chunk in chunks):
        raise TranslationError("Internal error: a source chunk exceeds its token budget.")
    if re.sub(r"\s+", "", "".join(chunks)) != re.sub(r"\s+", "", text):
        raise TranslationError("Internal error: source chunking changed document content.")
    return chunks


def preservation_diagnostics(source, translated):
    """Flag suspect omissions for inspection without inventing replacements.

    Digits rendered as number words or identifiers transliterated into Urdu
    may be valid. These are review flags, never claimed accuracy measurements.
    """
    warnings = []
    source_nfc = unicodedata.normalize("NFKC", source)
    translated_nfc = unicodedata.normalize("NFKC", translated)
    missing_numbers = Counter(_NUMBER.findall(source_nfc)) - Counter(
        _NUMBER.findall(translated_nfc))
    added_numbers = Counter(_NUMBER.findall(translated_nfc)) - Counter(
        _NUMBER.findall(source_nfc))
    if missing_numbers:
        warnings.append("Review numerical rendering: source digits absent: "
                        + ", ".join(missing_numbers.elements()))
    if added_numbers:
        warnings.append("Review numerical rendering: new output digits: "
                        + ", ".join(added_numbers.elements()))
    identifiers = sorted({item for item in _IDENTIFIER.findall(source_nfc)
                          if is_nonlinguistic_region(item)})
    missing_ids = [item for item in identifiers if item not in translated_nfc]
    if missing_ids:
        warnings.append("Review identifier rendering: " + ", ".join(missing_ids))
    return warnings


def prepare_translation_source(text, language):
    """Normalize unambiguous notation on a copy; original OCR stays intact."""
    if language == "chinese":
        # OCR inserts spaces at printed line wraps, even inside a Chinese word.
        text = re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "", text)
        digits = str.maketrans("〇零一二三四五六七八九", "00123456789")
        text = re.sub(r"[〇零一二三四五六七八九]{4}(?=年)",
                      lambda match: match.group().translate(digits), text)
    elif language == "english":
        # Only explicit measurement quantities, never arbitrary prose/identifiers.
        units = {word: index for index, word in enumerate(
            "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split())}
        tens = dict(zip("twenty thirty forty fifty sixty seventy eighty ninety".split(), range(20, 100, 10)))
        values = {**units, **tens}
        for word, value in tens.items():
            for small, addition in list(units.items())[1:10]:
                values[word + "-" + small] = value + addition
                values[word + " " + small] = value + addition
        alternatives = "|".join(re.escape(word) for word in sorted(values, key=len, reverse=True))
        pattern = r"(?<![\w-])(" + alternatives + r")(?=\s+(?:percent|degrees|milliseconds|minutes|seconds)\b)"
        text = re.sub(pattern, lambda match: str(values[match.group().lower()]), text, flags=re.I)
    return text


def translation_units(text, tokenizer):
    """Keep baseline sentence-sized requests; larger contexts lost real sentences."""
    parts = re.split(r"(?<=[。！？؟۔])\s*|(?<=[.!?])\s+", text.strip())
    chunks = [chunk for part in parts if part.strip()
              for chunk in chunk_paragraph(part, tokenizer)]
    if re.sub(r"\s+", "", "".join(chunks)) != re.sub(r"\s+", "", text):
        raise TranslationError("Sentence splitting lost source content")
    return chunks


def translate_regions(paragraphs, language, output_language, translator, tokenizer):
    """Return translated region copies and elapsed seconds; never reorder IDs."""
    started = time.perf_counter()
    if output_language not in {"english", "urdu"}:
        raise ValueError(f"Unsupported output language: {output_language}")
    regions = [dict(region) for region in paragraphs]
    ids = [region.get("region_id", region.get("number", index + 1))
           for index, region in enumerate(regions)]
    if len(set(ids)) != len(ids):
        raise TranslationError("Duplicate OCR region IDs; translation tracking is ambiguous.")
    document = "\n".join(str(region.get("source_text", "")) for region in regions)
    effective_language, language_warnings = resolve_source_language(document, language)
    for warning in language_warnings:
        print(f"[TRANSLATION LANGUAGE] {warning}")
    batches, owners = [], []
    for index, region in enumerate(regions):
        source = str(region.get("source_text", ""))
        region["region_id"] = ids[index]
        region["effective_source_language"] = effective_language
        region["translation_warnings"] = list(language_warnings)
        region["translation_chunk_count"] = 0
        if effective_language == output_language:
            region["translated_text"] = source
            region["translation_status"] = "bypassed_same_language"
        elif is_nonlinguistic_region(source):
            region["translated_text"] = source
            region["translation_status"] = "preserved_identifier"
        else:
            if translator is None or tokenizer is None:
                raise TranslationError("The selected language pair requires the local NLLB model.")
            tokenizer.src_lang = LANGUAGE_TOKENS[effective_language]
            prepared = prepare_translation_source(source, effective_language)
            region["translation_input"] = prepared
            chunks = translation_units(prepared, tokenizer)
            region["translation_chunk_count"] = len(chunks)
            for chunk in chunks:
                batches.append(tokenizer.convert_ids_to_tokens(_encoded(tokenizer, chunk)))
                owners.append(index)
    if batches:
        target_token = LANGUAGE_TOKENS[output_language]
        results = translator.translate_batch(
            batches, target_prefix=[[target_token] for _ in batches],
            beam_size=BEAM_SIZE, max_batch_size=MAX_BATCH_SIZE,
            batch_type="examples", max_input_length=MAX_SOURCE_TOKENS,
            max_decoding_length=MAX_DECODING_TOKENS,
            return_end_token=True)
        if len(results) != len(batches):
            raise TranslationError(
                f"NLLB returned {len(results)} results for {len(batches)} chunks; "
                "the partial translation was not sent to speech.")
        by_owner = [[] for _ in regions]
        for owner, result in zip(owners, results):
            hypotheses = getattr(result, "hypotheses", [])
            if not hypotheses or not hypotheses[0]:
                raise TranslationError(f"NLLB returned no text for region {ids[owner]}.")
            tokens = hypotheses[0]
            eos = getattr(tokenizer, "eos_token", "</s>")
            if len(tokens) >= MAX_DECODING_TOKENS and tokens[-1] != eos:
                raise TranslationError(
                    f"Region {ids[owner]} reached the NLLB output limit; "
                    "the incomplete translation was not sent to speech.")
            decoded = tokenizer.decode(
                tokenizer.convert_tokens_to_ids(tokens), skip_special_tokens=True).strip()
            if not decoded:
                raise TranslationError(f"NLLB returned an empty translation for region {ids[owner]}.")
            by_owner[owner].append(decoded)
        for index, parts in enumerate(by_owner):
            if not parts:
                continue
            region = regions[index]
            region["translated_text"] = " ".join(parts)
            region["translation_status"] = "translated"
            diagnostics = preservation_diagnostics(
                region.get("source_text", ""), region["translated_text"])
            region["translation_warnings"].extend(diagnostics)
            for diagnostic in diagnostics:
                print(f"[TRANSLATION REVIEW] Region {ids[index]}: {diagnostic}")
    elapsed = time.perf_counter() - started
    print(f"[STAGE 2] {len(regions)} regions, {len(batches)} NLLB chunks, "
          f"{effective_language} -> {output_language}: {elapsed:.3f}s")
    return regions, elapsed
