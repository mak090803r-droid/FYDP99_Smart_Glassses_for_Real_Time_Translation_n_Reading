"""No-model tests for Box6 source preservation and region mapping."""
import pathlib
import re
import sys
import types
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import box6_translation as translation


class CharacterTokenizer:
    src_lang = "fra_Latn"
    eos_token = "</s>"

    def encode(self, text, truncation=False):
        assert not truncation
        return ["<s>"] + list(text) + ["</s>"]

    def convert_ids_to_tokens(self, ids):
        return ids

    def convert_tokens_to_ids(self, tokens):
        return tokens

    def decode(self, tokens, skip_special_tokens=False):
        return "".join(token for token in tokens if not (
            skip_special_tokens and (token.startswith("<") or token.endswith("_Latn"))))


class FakeTranslator:
    def __init__(self, behaviour="echo"):
        self.behaviour = behaviour
        self.calls = []

    def translate_batch(self, source, **kwargs):
        self.calls.append((source, kwargs))
        if self.behaviour == "mismatch":
            return []
        if self.behaviour == "empty":
            return [types.SimpleNamespace(hypotheses=[["</s>"]]) for _ in source]
        if self.behaviour == "capped":
            return [types.SimpleNamespace(hypotheses=[["x"] * 512]) for _ in source]
        return [types.SimpleNamespace(hypotheses=[tokens]) for tokens in source]


class TranslationTests(unittest.TestCase):
    def setUp(self):
        self.tokenizer = CharacterTokenizer()

    def test_paragraph_context_retained(self):
        text = "This sentence has context. The next sentence refers to it."
        self.assertEqual(translation.chunk_paragraph(text, self.tokenizer), [text])

    def test_long_latin_and_cjk_content_preserved(self):
        for text in ["Bonjour à tous. " * 120, "这是测试段落。" * 150,
                     "https://example.test/" + "a" * 1200, "کیمرا متن پڑھتا ہے۔ " * 100]:
            chunks = translation.chunk_paragraph(text, self.tokenizer, max_tokens=50)
            self.assertTrue(all(len(self.tokenizer.encode(c)) <= 50 for c in chunks))
            self.assertEqual(re.sub(r"\s+", "", "".join(chunks)), re.sub(r"\s+", "", text))

    def test_stable_ids_and_input_untouched(self):
        original = [dict(region_id=7, source_text="Bonjour. Cette phrase continue."),
                    dict(region_id=12, source_text="Un autre texte.")]
        model = FakeTranslator()
        result, _ = translation.translate_regions(original, "french", "english", model, self.tokenizer)
        self.assertEqual([r["region_id"] for r in result], [7, 12])
        self.assertNotIn("translated_text", original[0])
        self.assertEqual([r["translated_text"] for r in result], [r["source_text"] for r in original])
        kwargs = model.calls[0][1]
        self.assertEqual(kwargs["max_decoding_length"], 512)
        self.assertEqual(kwargs["max_batch_size"], 8)
        self.assertEqual(kwargs["beam_size"], 2)

    def test_numbers_and_identifiers_skip_model(self):
        for text in ["1", "2025", "ISO 9001", "Li-ion", "UAS", "A4"]:
            model = FakeTranslator()
            result, _ = translation.translate_regions([dict(source_text=text)], "french", "english", model, self.tokenizer)
            self.assertEqual(result[0]["translated_text"], text)
            self.assertEqual(len(model.calls), 0)

    def test_empty_mismatch_and_limit_fail(self):
        for behaviour in ["empty", "mismatch", "capped"]:
            with self.assertRaises(translation.TranslationError):
                translation.translate_regions([dict(source_text="Bonjour le monde.")],
                    "french", "english", FakeTranslator(behaviour), self.tokenizer)

    def test_uppercase_heading_is_not_assumed_to_be_identifier(self):
        self.assertFalse(translation.is_nonlinguistic_region("STOP THE ENGINE"))
        self.assertFalse(translation.is_nonlinguistic_region("real-time"))

    def test_language_resolution_on_saved_english_under_chinese(self):
        source = pathlib.Path(__file__).resolve().parents[1] / "paragraph_test_outputs" / "run_001_20260903_134627_ocr.txt"
        content = source.read_text(encoding="utf-8")
        text = "\n".join(re.findall(r"^  SOURCE\s*:\s*(.+)$", content, re.MULTILINE))
        effective, warnings = translation.resolve_source_language(text, "chinese")
        self.assertEqual(effective, "english")
        self.assertTrue(warnings)
        model = FakeTranslator()
        result, _ = translation.translate_regions([dict(source_text=text)], "chinese", "english", model, self.tokenizer)
        self.assertEqual(result[0]["translated_text"], text)
        self.assertFalse(model.calls)

    def test_no_uncertain_romance_language_switch(self):
        french = ("Les lunettes permettent de lire les documents dans une pièce. "
                  "Le texte est traduit pour une personne avec un appareil local. "
                  "Cette image contient des mots français et les nombres sont conservés. ") * 4
        for selected in ["chinese", "french", "spanish"]:
            effective, _ = translation.resolve_source_language(french, selected)
            self.assertEqual(effective, selected)
        self.assertEqual(translation.resolve_source_language("Hello", "chinese")[0], "chinese")

    def test_same_language_with_no_model(self):
        text = "Original English must remain intact."
        result, _ = translation.translate_regions([dict(source_text=text)], "english", "english", None, None)
        self.assertEqual(result[0]["translated_text"], text)

    def test_review_flags_never_change_numbers(self):
        self.assertTrue(translation.preservation_diagnostics("UAS uses 2025 data at 34%.", "Aircraft uses 2005 data at 43%."))

    def test_duplicate_ids_fail(self):
        with self.assertRaises(translation.TranslationError):
            translation.translate_regions([dict(region_id=1, source_text="a"), dict(region_id=1, source_text="b")],
                "english", "english", None, None)

    def test_notation_cleanup_preserves_original_and_quantities(self):
        self.assertEqual(translation.prepare_translation_source(
            "二〇二五年微电 网", "chinese"), "2025年微电网")
        self.assertEqual(translation.prepare_translation_source(
            "thirty-four percent and eight degrees Celsius. One engineer.", "english"),
            "34 percent and 8 degrees Celsius. One engineer.")
        self.assertEqual(translation.prepare_translation_source("B-thirty-four percent", "english"),
                         "B-thirty-four percent")

    def test_sentence_requests_keep_all_content_and_tracking(self):
        text = "这是第一句。这是第二句。"
        self.assertEqual(translation.translation_units(text, self.tokenizer),
                         ["这是第一句。", "这是第二句。"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
