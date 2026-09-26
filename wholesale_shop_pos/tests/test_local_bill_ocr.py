from odoo.addons.wholesale_shop_pos.models import local_bill_ocr
from odoo.tests.common import TransactionCase


class TestLocalBillOCRParser(TransactionCase):
    def _token(self, text, x, y, width=45, score=0.98):
        return local_bill_ocr._token(
            text,
            score,
            [[x, y], [x + width, y], [x + width, y + 16], [x, y + 16]],
        )

    def test_coordinate_parser_extracts_metadata_and_table_values(self):
        tokens = [
            self._token("SNK PILLAI AGENCIES", 15, 30, 190),
            self._token("GSTN NO: 33AEDPM7319H1ZL", 15, 55, 240),
            self._token("Bill No: AAI15646", 520, 55, 180),
            self._token("Bill Date: 04/08/2026", 520, 80, 210),
            self._token("Sl", 10, 190, 25),
            self._token("HSN NO", 55, 190, 65),
            self._token("Product Name", 155, 190, 125),
            self._token("UPC", 285, 190, 45),
            self._token("MRP", 335, 190, 45),
            self._token("Cs", 390, 190, 30),
            self._token("Pcs", 430, 190, 40),
            self._token("Base Rate", 475, 190, 70),
            self._token("Sch Disc", 545, 190, 65),
            self._token("RS Disc", 610, 190, 60),
            self._token("Taxable Amt", 670, 190, 75),
            self._token("GST %", 745, 190, 45),
            self._token("CGST", 795, 190, 45),
            self._token("SGST", 850, 190, 45),
            self._token("Net", 930, 190, 45),
        ]

        def row(y, serial, name, hsn, pieces, rate, gst):
            return [
                self._token(str(serial), 15, y, 15),
                self._token(hsn, 55, y, 70),
                self._token(name, 155, y, 120),
                self._token("120", 290, y, 35),
                self._token("92.00", 335, y, 45),
                self._token("0", 395, y, 15),
                self._token(pieces, 435, y, 35),
                self._token(str(rate), 480, y, 55),
                self._token("0.00", 550, y, 45),
                self._token("0.00", 615, y, 45),
                self._token(str(float(rate) * 10), 675, y, 60),
                self._token(str(gst), 750, y, 25),
                self._token("20.47", 800, y, 45),
                self._token("20.47", 855, y, 45),
                self._token("859.84", 930, y, 55),
            ]

        tokens += row(235, 1, "3ROSES 100G", "09023020", "10+2", 81.89, 5)
        tokens += row(270, 2, "RIN SOAP", "34011930", "24", 15.69, 18)
        tokens.append(self._token("Total", 155, 315, 60))

        parsed = local_bill_ocr._parse_page(tokens, 1000, 400)

        self.assertEqual(parsed["vendor_tax_id"], "33AEDPM7319H1ZL")
        self.assertEqual(parsed["bill_number"], "AAI15646")
        self.assertEqual(parsed["bill_date"], "2026-08-04")
        self.assertEqual(len(parsed["lines"]), 2)
        first = parsed["lines"][0]
        self.assertEqual(first["description"], "3ROSES 100G")
        self.assertEqual(first["quantity"], 10)
        self.assertEqual(first["free_quantity"], 2)
        self.assertAlmostEqual(first["purchase_rate"], 81.89, places=2)
        self.assertEqual(first["gst_percent"], 5)

        # A close phone photo can cut off every serial number while retaining
        # the HSN and product columns. Those rows must still be reconstructed.
        cropped_tokens = [
            token
            for token in tokens
            if not (
                token["x0"] < 40
                and local_bill_ocr._normalise_key(token["text"]) in ("1", "2")
            )
        ]
        cropped = local_bill_ocr._parse_page(cropped_tokens, 1000, 400)
        self.assertEqual(len(cropped["lines"]), 2)
        self.assertEqual(cropped["lines"][0]["hsn_code"], "09023020")

    def test_dynamic_stacked_header_extracts_upper_and_lower_value_rows(self):
        aliases = {
            "serial": ["#"],
            "description": ["Item Name"],
            "hsn": ["HSN"],
            "uom": ["UOM"],
            "secondary_quantity": ["Qty in SUOM"],
            "mrp": ["MRP"],
            "taxable": ["Taxable Amt"],
            "rate": ["Rate"],
            "cgst_percent": ["CGST %"],
            "cgst_amount": ["CGST Amt"],
            "quantity": ["Qty"],
            "gross_amount": ["GrossAmt"],
            "sgst_percent": ["SGST%"],
            "free_quantity": ["Free"],
            "sgst_amount": ["SGST Amt"],
            "discount_percent": ["Disc%"],
            "discount_amount": ["Disc.Amt"],
            "other_discount": ["Other Disc"],
            "tax_amount": ["Tot.Tax"],
            "net": ["Amount"],
        }
        tokens = [
            self._token("#", 10, 190, 15),
            self._token("Item Name", 70, 190, 120),
            self._token("HSN", 70, 212, 50),
            self._token("Qty in SUOM", 250, 212, 105),
            self._token("UOM", 400, 190, 55),
            self._token("Taxable Amt", 400, 212, 90),
            self._token("MRP", 500, 190, 45),
            self._token("CGST %", 500, 212, 60),
            self._token("Rate", 575, 190, 45),
            self._token("CGST Amt", 575, 212, 75),
            self._token("Qty", 655, 190, 40),
            self._token("GrossAmt", 720, 190, 75),
            self._token("SGST%", 720, 212, 55),
            self._token("Free", 810, 190, 45),
            self._token("SGST Amt", 810, 212, 75),
            self._token("Disc%", 895, 190, 50),
            self._token("Disc.Amt", 955, 190, 70),
            self._token("Amount", 1040, 190, 65),
        ]

        def stacked_row(y, serial, name, hsn, mrp, rate, quantity, taxable, tax):
            return [
                self._token(str(serial), 10, y, 15),
                self._token(name, 70, y, 285),
                self._token("PAC", 410, y, 40),
                self._token(str(mrp), 500, y, 45),
                self._token(str(rate), 575, y, 50),
                self._token(str(quantity), 655, y, 45),
                self._token(str(round(rate * quantity, 2)), 720, y, 65),
                self._token("0.00", 810, y, 45),
                self._token("0.00", 895, y, 45),
                self._token("0.00", 955, y, 45),
                self._token(str(round(taxable * 1.05, 2)), 1040, y, 60),
                self._token(hsn, 70, y + 22, 75),
                self._token("0.828000KG", 250, y + 22, 95),
                self._token(str(taxable), 400, y + 22, 65),
                self._token(str(tax), 500, y + 22, 40),
                self._token(str(round(taxable * tax / 100, 2)), 575, y + 22, 50),
                self._token(str(tax), 720, y + 22, 40),
                self._token(str(round(taxable * tax / 100, 2)), 810, y + 22, 50),
            ]

        tokens += stacked_row(250, 1, "DARK FANTASY CHOCOFILLS", "19053100", 35, 29.76, 12, 357.12, 2.5)
        tokens += stacked_row(300, 2, "BNC DAY N NIGHT CHOCOVAN", "19053100", 5, 4.36, 36, 156.96, 2.5)
        tokens.append(self._token("Total", 70, 360, 60))

        alias_token = local_bill_ocr._HEADER_ALIASES.set(aliases)
        template_token = local_bill_ocr._OCR_TEMPLATE.set({
            "strict": True,
            "aliases": aliases,
            "data_rows_per_item": 2,
            "vendor_name": "Dinesh Enterprises",
        })
        try:
            parsed = local_bill_ocr._parse_page(tokens, 1120, 420)
        finally:
            local_bill_ocr._OCR_TEMPLATE.reset(template_token)
            local_bill_ocr._HEADER_ALIASES.reset(alias_token)

        self.assertEqual(len(parsed["lines"]), 2)
        first = parsed["lines"][0]
        self.assertEqual(first["description"], "DARK FANTASY CHOCOFILLS")
        self.assertEqual(first["hsn_code"], "19053100")
        self.assertEqual(first["quantity"], 12)
        self.assertAlmostEqual(first["purchase_rate"], 29.76)
        self.assertEqual(first["gst_percent"], 5.0)
