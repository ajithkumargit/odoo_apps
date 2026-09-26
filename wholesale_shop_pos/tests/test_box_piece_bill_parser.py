"""Regression coverage for photographed box/piece invoices, without OCR inference."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location(
    "box_piece_ocr", Path(__file__).parents[1] / "models" / "local_bill_ocr.py"
)
ocr = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ocr)


class TestBoxPieceBillParser(unittest.TestCase):
    def setUp(self):
        self.page = json.loads((Path(__file__).parent / "fixtures" / "box_piece_table_tokens.json").read_text())

    def parse(self, tokens=None):
        return ocr._parse_page(tokens or self.page["tokens"], self.page["width"], self.page["height"])

    def test_merged_names_rates_and_grouped_tax_headers(self):
        result = self.parse()
        self.assertEqual(len(result["lines"]), 3)
        self.assertEqual([line["quantity"] for line in result["lines"]], [9, 1, 20])
        self.assertEqual([line["purchase_rate"] for line in result["lines"]], [1698.09, 1719.05, 15.37])
        for line, taxable in zip(result["lines"], [15240.08, 1714.24, 306.44]):
            self.assertEqual(line["hsn_code"], "15121910")
            self.assertEqual(line["gst_percent"], 5)
            self.assertNotIn("15121910", line["description"])
            self.assertAlmostEqual(line["quantity"] * line["purchase_rate"] * (1 - line["discount_percent"] / 100), taxable, delta=.11)
        self.assertEqual(sum("priced per box" in warning for warning in result["warnings"]), 2)
        self.assertTrue(any("rounded printed unit price" in warning for warning in result["warnings"]))

    def test_inconsistent_printed_amount_is_rejected(self):
        tokens = copy.deepcopy(self.page["tokens"])
        next(t for t in tokens if t["text"] == "15240.08")["text"] = "1524.08"
        result = self.parse(tokens)
        self.assertEqual(len(result["lines"]), 2)
        self.assertTrue(any("totals disagree" in warning for warning in result["warnings"]))

    def test_mixed_boxes_and_pieces_without_pack_size_are_rejected(self):
        tokens = copy.deepcopy(self.page["tokens"])
        next(t for t in tokens if t["text"] == "0" and 695 < t["yc"] < 705)["text"] = "2"
        result = self.parse(tokens)
        self.assertEqual(len(result["lines"]), 2)
        self.assertTrue(any("mixed or zero" in warning for warning in result["warnings"]))

    def test_missing_tax_rate_does_not_guess(self):
        tokens = [t for t in self.page["tokens"] if not (t["text"] == "2.50" and 700 < t["yc"] < 709)]
        self.assertEqual(len(self.parse(tokens)["lines"]), 2)


if __name__ == "__main__":
    unittest.main()
