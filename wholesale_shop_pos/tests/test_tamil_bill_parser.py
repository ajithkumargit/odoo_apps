"""Deterministic bilingual bill parsing; runnable without Odoo or OCR engines."""

import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch


_SPEC = importlib.util.spec_from_file_location(
    "local_bill_ocr_tamil_tests",
    Path(__file__).resolve().parents[1] / "models" / "local_bill_ocr.py",
)
ocr = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ocr)


class TestTamilBillParser(unittest.TestCase):
    def test_configurable_header_alias(self):
        token = ocr._HEADER_ALIASES.set({
            "description": ["Article Details"], "quantity": ["Billed Qty"],
        })
        try:
            self.assertEqual(ocr._table_header_role("Article Details"), "description")
            joined = ocr._join_table_header_tokens([
                self.token("Billed", 500, 100), self.token("Qty", 552, 100),
            ], 16)
            self.assertEqual(len(joined), 1)
            self.assertEqual(ocr._table_header_role(joined[0]["text"]), "quantity")
        finally:
            ocr._HEADER_ALIASES.reset(token)

    def test_strict_template_controls_metadata_and_headers(self):
        aliases = {
            "bill_number": ["Document Ref"],
            "bill_date": ["Document Day"],
            "description": ["Article Details"],
        }
        alias_token = ocr._HEADER_ALIASES.set(aliases)
        template_token = ocr._OCR_TEMPLATE.set({
            "strict": True,
            "vendor_name": "Configured Vendor",
            "vendor_tax_id": "33ABCDE1234F1Z5",
        })
        try:
            self.assertIsNone(ocr._table_header_role("Product Name"))
            self.assertEqual(ocr._table_header_role("Article Details"), "description")
            tokens = [
                self.token("Document Ref", 500, 20, 110),
                self.token("INV-2048", 630, 20, 100),
                self.token("Document Day", 500, 55, 110),
                self.token("9-Sep-26", 630, 55, 100),
            ]
            lines = ocr._group_lines(tokens)
            self.assertEqual(ocr._bill_number_from_tokens(tokens), "INV-2048")
            self.assertEqual(ocr._bill_date(lines), "2026-09-09")
            self.assertEqual(ocr._vendor_name(lines, 1000), "Configured Vendor")
        finally:
            ocr._OCR_TEMPLATE.reset(template_token)
            ocr._HEADER_ALIASES.reset(alias_token)

    def test_tally_invoice_units_two_rates_and_footer_gst(self):
        tokens = [
            self.token("Sl No.", 10, 100, 45),
            self.token("Description of Goods", 90, 100, 210),
            self.token("HSN/SAC", 410, 100, 80),
            self.token("Quantity", 525, 100, 80),
            self.token("Rate (Incl. of Tax)", 650, 100, 115),
            self.token("Rate", 800, 100, 60),
            self.token("Amount", 950, 100, 75),
            self.token("1", 20, 150, 20),
            self.token("TC Groundnut Oil 1 Ltr C [10 Ltr]", 90, 150, 245),
            self.token("15089091", 410, 150, 75),
            self.token("1 Case", 525, 150, 70),
            self.token("2,200.00", 650, 150, 85),
            self.token("2,095.24", 800, 150, 85),
            self.token("2,095.24", 950, 150, 85),
            self.token("2", 20, 190, 20),
            self.token("TC Coconut Oil 200ml C - [P1]", 90, 190, 230),
            self.token("15131900", 410, 190, 75),
            self.token("10.000 PCS", 525, 190, 90),
            self.token("65.00", 650, 190, 60),
            self.token("61.90", 800, 190, 60),
            self.token("619.00", 950, 190, 70),
            self.token("Output CGST @ 2.5%", 580, 245, 145),
            self.token("Output SGST @ 2.5%", 580, 270, 145),
        ]
        rows, warnings = ocr._extract_table_lines(tokens, 1100)
        self.assertEqual(len(rows), 2, warnings)
        self.assertEqual((rows[0]["quantity"], rows[0]["purchase_rate"]), (1, 2095.24))
        self.assertEqual((rows[1]["quantity"], rows[1]["purchase_rate"]), (10, 61.9))
        self.assertEqual([row["gst_percent"] for row in rows], [5, 5])

    def test_tally_perspective_coordinates_from_anand_bill(self):
        def actual(text, x, y, width, height=42):
            return ocr._token(text, .95, [
                [x - width / 2, y - height / 2], [x + width / 2, y - height / 2],
                [x + width / 2, y + height / 2], [x - width / 2, y + height / 2],
            ])

        tokens = [
            actual("HSN/SAC", 1160, 740, 165, 38), actual("Quantity", 1350, 740, 135, 43),
            actual("Rate", 1542, 738, 83, 34), actual("Rate", 1730, 736, 85, 38),
            actual("Amount", 2034, 735, 123, 36), actual("Sl", 111, 750, 54, 45),
            actual("Description of Goods", 596, 747, 323, 38),
            actual("(Incl. of Tax)", 1544, 778, 178, 43), actual("No.", 112, 794, 56, 49),
            actual("1 Case", 1379, 845, 134), actual("2,200.00", 1562, 840, 149),
            actual("2,095.24", 1752, 839, 149), actual("Case", 1870, 837, 73, 34),
            actual("2,095.24", 2095, 836, 156),
            actual("TC Groundnut Oil 1 Ltr C [10 Ltr]", 478, 855, 658, 52),
            actual("15089091", 1152, 844, 161), actual("1", 122, 858, 23, 27),
            actual("1 Case", 1384, 884, 124), actual("Location: Main Location", 390, 899, 424),
            actual("10.000 PCS", 1354, 928, 186, 48), actual("65.00", 1586, 924, 97),
            actual("61.90", 1781, 922, 104), actual("PCS", 1864, 919, 88),
            actual("15131900", 1155, 928, 166),
            actual("619.00", 2114, 919, 122), actual("TC Coconut Oil 200ml C - [P]", 444, 942, 579, 51),
            actual("2", 133, 947, 32, 34), actual("10.000 PCS", 1354, 968, 178, 38),
            actual("Location: Main Location", 396, 982, 421),
            actual("Output CGST @ 2.5%", 854, 1358, 420, 67),
            actual("Output SGST @ 2.5%", 856, 1398, 415, 62),
        ]
        alias_token = ocr._HEADER_ALIASES.set({
            "serial": ["Sl", "Sl No"],
            "description": ["Description of Goods"],
            "hsn": ["HSN/SAC"],
            "quantity": ["Quantity", "PCS"],
            "case": ["Case"],
            "rate": ["Rate"],
            "tax_inclusive_rate": ["Rate Incl. of Tax"],
            "net": ["Amount"],
        })
        template_token = ocr._OCR_TEMPLATE.set({"strict": True})
        try:
            rows, warnings = ocr._extract_table_lines(tokens, 2300)
        finally:
            ocr._OCR_TEMPLATE.reset(template_token)
            ocr._HEADER_ALIASES.reset(alias_token)
        self.assertEqual(len(rows), 2, warnings)
        self.assertEqual((rows[0]["quantity"], rows[0]["purchase_rate"]), (1, 2095.24))
        self.assertEqual((rows[1]["quantity"], rows[1]["purchase_rate"]), (10, 61.9))
        self.assertEqual([row["gst_percent"] for row in rows], [5, 5])

    def test_separate_free_and_discount_columns(self):
        headers = ["S.No", "Particulars", "MRP", "QTY", "FREE", "HSN", "Rate", "Dis%", "GST%", "Amount"]
        values = ["1", "Chicken masala", "0.00", "5.000", "0.500", "09109100", "340.00", "2", "5.00", "1749.30"]
        xs = [10, 100, 320, 410, 500, 600, 710, 810, 910, 1010]
        tokens = [self.token(label, x, 100, 65) for label, x in zip(headers, xs)]
        tokens += [self.token(value, x, 150, 65) for value, x in zip(values, xs)]
        rows, warnings = ocr._extract_table_lines(tokens, 1150)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["quantity"], 5)
        self.assertEqual(rows[0]["free_quantity"], .5)
        self.assertEqual(rows[0]["discount_percent"], 2)
        self.assertEqual(rows[0]["gst_percent"], 5)
        self.assertEqual(warnings, [])

    def test_net_rate_is_not_total_column(self):
        self.assertEqual(ocr._table_header_role("Net Rate"), "tax_inclusive_rate")
        self.assertEqual(ocr._table_header_role("TOTAL"), "net")
        labels = [self.token("Net", 700, 100), self.token("Rate", 700, 118)]
        joined = ocr._join_table_header_tokens(labels, 16)
        self.assertEqual(len(joined), 1)
        self.assertEqual(ocr._table_header_role(joined[0]["text"]), "tax_inclusive_rate")
        labels = [self.token("C.D", 700, 100), self.token("Amt", 700, 136)]
        joined = ocr._join_table_header_tokens(labels, 16)
        self.assertEqual(len(joined), 1)
        self.assertEqual(ocr._table_header_role(joined[0]["text"]), "cash_discount")

    def test_missing_quantity_decimal_is_not_imported_as_hundreds(self):
        parsed = ocr._parse_page(self.simple_table(quantity="0600", rate="500"), 1000, 300)
        self.assertEqual(parsed["lines"], [])
        self.assertTrue(any("decimal points" in warning for warning in parsed["warnings"]))

    def test_slanted_header_keeps_distant_tax_column(self):
        tokens = self.simple_table()
        for token in tokens:
            if 100 <= token["yc"] <= 230:
                offset = -.06 * token["xc"]
                for key in ("y0", "y1", "yc"):
                    token[key] += offset
        header = ocr._find_table_header(tokens)
        roles = {ocr._table_header_role(token["text"]) for token in header[1]}
        self.assertIn("gst", roles)
        parsed = ocr._parse_page(tokens, 1000, 300)
        self.assertEqual(len(parsed["lines"]), 1)
        self.assertEqual(parsed["lines"][0]["gst_percent"], 5)

    def test_case_column_does_not_select_snk_layout(self):
        tokens = self.simple_table()
        tokens.append(self.token("Case", 350, 110, 45))
        with patch.object(ocr, "_extract_labelled_table_lines", return_value=([], [])) as parser:
            ocr._extract_table_lines(tokens, 1000)
        parser.assert_called_once()

    def token(self, text, x, y, width=50):
        return ocr._token(text, 0.98, [[x, y], [x + width, y], [x + width, y + 16], [x, y + 16]])

    def simple_table(self, tax_label="வரி %", tax="5", quantity="10", rate="20.00"):
        return [
            self.token("ஸ்ரீ முருகன் மளிகை", 20, 20, 220),
            self.token("பில் எண்: TM/142", 620, 20, 190),
            self.token("தேதி: 11/09/2026", 620, 55, 190),
            self.token("வ.எண்", 10, 110, 40),
            self.token("பொருள் பெயர்", 120, 110, 180),
            self.token("அளவு", 430, 110, 55),
            self.token("விலை", 560, 110, 55),
            self.token(tax_label, 700, 110, 55),
            self.token("தொகை", 850, 110, 70),
            self.token("1", 15, 155, 25),
            self.token("அரிசி பொன்னி 1KG", 120, 155, 185),
            self.token(quantity, 440, 155, 35),
            self.token(rate, 560, 155, 60),
            self.token(tax, 710, 155, 35),
            self.token("210.00", 850, 155, 65),
            self.token("மொத்தம்", 120, 215, 120),
            self.token("10", 440, 215, 35),
            self.token("210.00", 850, 215, 65),
        ]

    def test_tamil_keys_preserve_vowels_and_descriptions(self):
        self.assertEqual(ocr._normalise_key("பொருள் பெயர்:"), "பொருள்பெயர்")
        self.assertNotEqual(ocr._normalise_key("கல்"), ocr._normalise_key("கால்"))
        self.assertTrue(ocr._has_tamil("Ponni அரிசி 1KG"))
        self.assertFalse(ocr._has_tamil("Ponni Rice 1KG"))
        self.assertEqual(ocr._description([self.token("பொன்னி அரிசி", 10, 10, 150)]), "பொன்னி அரிசி")

    def test_tamil_metadata_and_simple_table(self):
        parsed = ocr._parse_page(self.simple_table(), 1000, 300)
        self.assertEqual(parsed["vendor_name"], "ஸ்ரீ முருகன் மளிகை")
        self.assertEqual(parsed["bill_number"], "TM/142")
        self.assertEqual(parsed["bill_date"], "2026-09-11")
        self.assertEqual(len(parsed["lines"]), 1)
        row = parsed["lines"][0]
        self.assertEqual(row["description"], "அரிசி பொன்னி 1KG")
        self.assertEqual(row["quantity"], 10)
        self.assertEqual(row["purchase_rate"], 20)
        self.assertEqual(row["gst_percent"], 5)
        self.assertEqual(parsed["warnings"], [])

    def test_mixed_language_rows_keep_individual_taxes_and_free_quantity(self):
        tokens = self.simple_table(quantity="10+2")
        tokens.extend([
            self.token("2", 15, 185, 25),
            self.token("LUX சோப்பு 100G", 120, 185, 185),
            self.token("3", 440, 185, 35),
            self.token("35.27", 560, 185, 60),
            self.token("18", 710, 185, 35),
            self.token("124.86", 850, 185, 65),
        ])
        parsed = ocr._parse_page(tokens, 1000, 300)
        self.assertEqual(len(parsed["lines"]), 2)
        first, second = parsed["lines"]
        self.assertEqual((first["quantity"], first["free_quantity"], first["gst_percent"]), (10, 2, 5))
        self.assertEqual(second["description"], "LUX சோப்பு 100G")
        self.assertEqual((second["quantity"], second["purchase_rate"], second["gst_percent"]), (3, 35.27, 18))

    def test_tax_amount_is_not_misread_as_tax_percentage(self):
        parsed = ocr._parse_page(self.simple_table(tax_label="வரித்தொகை", tax="5.00"), 1000, 300)
        self.assertEqual(len(parsed["lines"]), 1)
        self.assertEqual(parsed["lines"][0]["gst_percent"], 0)
        self.assertTrue(any("Tax percentage" in warning for warning in parsed["warnings"]))

    def test_unreadable_quantity_or_rate_is_not_invented(self):
        for field in ("quantity", "rate"):
            with self.subTest(field=field):
                parsed = ocr._parse_page(self.simple_table(**{field: "?"}), 1000, 300)
                self.assertEqual(parsed["lines"], [])
                self.assertTrue(any("unclear" in warning for warning in parsed["warnings"]))

    def test_explicit_zero_cost_free_item_is_retained(self):
        parsed = ocr._parse_page(self.simple_table(quantity="0+2", rate="0.00", tax="0"), 1000, 300)
        row = parsed["lines"][0]
        self.assertEqual((row["quantity"], row["free_quantity"], row["purchase_rate"], row["gst_percent"]), (0, 2, 0, 0))
        self.assertEqual(parsed["warnings"], [])

    def test_short_tamil_bill_number_and_invalid_date(self):
        self.assertEqual(ocr._bill_number([{"text": "ரசீது எண்: 42"}]), "42")
        self.assertIsNone(ocr._bill_date([{"text": "தேதி: 31/02/2026"}]))

    def test_explicit_three_percent_is_not_changed_by_hsn(self):
        lines = [
            {"description": "தேயிலை", "hsn_code": "09023010", "quantity": 2, "purchase_rate": 10, "gst_percent": 3.0},
            {"description": "Free தேயிலை", "hsn_code": "09023010", "quantity": 2, "purchase_rate": 0, "gst_percent": 0.0},
        ]
        with patch.object(ocr, "_extract_table_lines", return_value=(lines, [])):
            parsed = ocr._parse_page([], 1000, 300)
        self.assertEqual(parsed["lines"][0]["gst_percent"], 3.0)
        self.assertEqual(parsed["lines"][1]["gst_percent"], 0.0)

    def test_separate_percent_token_still_identifies_tamil_tax_rate(self):
        tokens = self.simple_table(tax_label="வரி")
        tokens.append(self.token("%", 760, 110, 10))
        parsed = ocr._parse_page(tokens, 1000, 300)
        self.assertEqual(parsed["lines"][0]["gst_percent"], 5)
        self.assertEqual(parsed["warnings"], [])

    def test_tax_component_percentages_are_combined_only_when_labelled(self):
        tokens = self.simple_table(tax_label="CGST %", tax="2.5")
        tokens.extend([
            self.token("SGST %", 785, 110, 55),
            self.token("2.5", 790, 155, 35),
        ])
        parsed = ocr._parse_page(tokens, 1000, 300)
        self.assertEqual(parsed["lines"][0]["gst_percent"], 5)

    def test_dense_snk_layout_and_split_free_quantity(self):
        headers = [
            ("Sl", 15, 20), ("HSN NO", 55, 70), ("Product Name", 155, 120),
            ("UPC", 290, 35), ("MRP", 335, 45), ("Cs", 395, 15),
            ("Pcs", 435, 35), ("Base Rate", 480, 55), ("Sch Disc", 550, 45),
            ("RS Disc", 615, 45), ("Taxable Amt", 675, 60), ("GST %", 750, 25),
            ("CGST", 800, 45), ("SGST", 855, 45), ("Net", 930, 55),
        ]
        tokens = [self.token(text, x, 100, width) for text, x, width in headers]
        values = ["1", "09023010", "தேயிலை", "120", "92.00", "0", "10+2", "81.89", "0.00", "0.00", "818.90", "3", "20.47", "20.47", "859.84"]
        tokens.extend(self.token(value, x, 145, width) for value, (_label, x, width) in zip(values, headers))
        tokens.append(self.token("Total", 155, 200, 80))
        parsed = ocr._parse_page(tokens, 1000, 250)
        self.assertEqual(len(parsed["lines"]), 1)
        row = parsed["lines"][0]
        self.assertEqual((row["quantity"], row["free_quantity"], row["gst_percent"]), (10, 2, 3))
        self.assertEqual(row["description"], "தேயிலை")
        self.assertTrue(any("disagree" in warning for warning in parsed["warnings"]))
        quantity_tokens = [self.token("10", 430, 100, 15), self.token("+", 446, 100, 5), self.token("2", 452, 100, 10)]
        self.assertEqual(ocr._parse_quantity(quantity_tokens), (10, 2))

    def test_english_simple_table_also_uses_detected_columns(self):
        replacements = {"வ.எண்": "S.No", "பொருள் பெயர்": "Description", "அளவு": "Qty", "விலை": "Unit Price", "வரி %": "GST %", "தொகை": "Amount"}
        tokens = [{**token, "text": replacements.get(token["text"], token["text"])} for token in self.simple_table()]
        parsed = ocr._parse_page(tokens, 1000, 300)
        self.assertEqual(len(parsed["lines"]), 1)
        self.assertEqual(parsed["lines"][0]["purchase_rate"], 20)
        self.assertEqual(parsed["lines"][0]["gst_percent"], 5)


if __name__ == "__main__":
    unittest.main()
