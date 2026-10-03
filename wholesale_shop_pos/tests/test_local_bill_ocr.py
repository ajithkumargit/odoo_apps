from odoo.addons.wholesale_shop_pos.models import local_bill_ocr
from odoo.tests.common import TransactionCase


class TestLocalBillOCRParser(TransactionCase):
    def _token(self, text, x, y, width=45, score=0.98):
        return local_bill_ocr._token(
            text,
            score,
            [[x, y], [x + width, y], [x + width, y + 16], [x, y + 16]],
        )

    def test_product_full_name_and_pieces_invoice(self):
        aliases = {
            'serial': ['S.No'], 'description': ['Product Full Name'],
            'hsn': ['HSN Code'], 'mrp': ['New MRP'],
            'case': ['Case'], 'quantity': ['Pieces'],
            'free_quantity': ['Free Qty'], 'rate': ['Rate'],
            'taxable': ['Taxable Amt'], 'cgst_percent': ['CGST%'],
            'sgst_percent': ['SGST%'], 'net': ['Total'],
        }
        tokens = [
            self._token('S.No', 10, 190, 30),
            self._token('Product', 55, 190, 65),
            self._token('Full', 125, 190, 35),
            self._token('Name', 165, 190, 45),
            self._token('HSN Code', 360, 190, 75),
            self._token('Old MRP', 570, 190, 65),
            self._token('New MRP', 655, 190, 70),
            self._token('Case', 745, 190, 40),
            self._token('Pieces', 805, 190, 50),
            self._token('Free Qty', 875, 190, 55),
            self._token('Rate', 950, 190, 40),
            self._token('Taxable Amt', 1080, 190, 90),
            self._token('SGST%', 1185, 190, 55),
            self._token('CGST%', 1250, 190, 55),
            self._token('Total', 1340, 190, 45),
        ]
        for y, number, name, mrp, pieces, free, rate, amount in [
            (240, 1, 'Stayfree Secure Ultra 6s', 54, 3, 0, 48.21, 144.63),
            (280, 2, 'SF Secure Dry XL6', 50, 9, 1, 44.64, 401.76),
            (320, 3, 'SF Secure Cottony Regular 6', 37, 11, 1, 33.04, 352.54),
        ]:
            tokens += [
                self._token(str(number), 10, y, 20),
                self._token(name, 55, y, 275),
                self._token('96190010', 360, y, 70),
                self._token(f'{mrp:.2f}', 655, y, 55),
                self._token('0', 745, y, 20),
                self._token(str(pieces), 805, y, 20),
                self._token(str(free), 875, y, 20),
                self._token(f'{rate:.2f}', 950, y, 50),
                self._token(f'{amount:.2f}', 1080, y, 60),
                self._token('0.00', 1185, y, 45),
                self._token('0.00', 1250, y, 45),
                self._token(f'{amount:.2f}', 1340, y, 60),
            ]
        tokens.append(self._token('Total Quantity', 700, 370, 130))
        alias_token = local_bill_ocr._HEADER_ALIASES.set(aliases)
        template_token = local_bill_ocr._OCR_TEMPLATE.set({
            'strict': True, 'aliases': aliases, 'data_rows_per_item': 1,
        })
        try:
            parsed = local_bill_ocr._parse_page(tokens, 1450, 420)
        finally:
            local_bill_ocr._OCR_TEMPLATE.reset(template_token)
            local_bill_ocr._HEADER_ALIASES.reset(alias_token)
        self.assertEqual(len(parsed['lines']), 3, parsed['warnings'])
        self.assertEqual([line['quantity'] for line in parsed['lines']], [3, 9, 11])
        self.assertEqual([line['mrp'] for line in parsed['lines']], [54, 50, 37])

    def test_item_description_pc_price_without_hsn_column(self):
        aliases = {
            'serial': ['S.No'], 'description': ['Item Description'],
            'mrp': ['MRP'], 'case': ['Cs'], 'quantity': ['Pcs'],
            'upc': ['UPC'], 'rate': ['Pc Price'],
            'gross_amount': ['Gross Amt'], 'scheme_discount': ['SCH Amt'],
            'discount_amount': ['Disc Amt'], 'taxable': ['Taxable Amt'],
            'gst': ['GST %'], 'net': ['Net Amt'],
        }
        tokens = [
            self._token('Pcs', 610, 90, 35),
            self._token('Gross Amt', 795, 90, 80),
            self._token('27', 610, 120, 30),
            self._token('1552.13', 795, 120, 65),
        ]
        for label, x, width in [
            ('S.No', 5, 35), ('Item Description', 50, 220),
            ('MRP', 500, 45), ('Cs', 565, 30), ('Pcs', 610, 35),
            ('UPC', 655, 45), ('Pc Price', 710, 75),
            ('Gross Amt', 795, 80), ('SCH Amt', 875, 70),
            ('Disc Amt', 950, 70), ('Taxable Amt', 1030, 90),
            ('GST %', 1110, 55), ('Net Amt', 1330, 70),
        ]:
            tokens.append(self._token(label, x, 190, width))
        for y, number, name, mrp, qty, upc, rate, gross, scheme, taxable, gst, net in [
            (245, 1, 'VAPORUB 5GM', 23, 5, 1200, 18.28, 91.40, 4.57, 86.83, 5, 91.17),
            (290, 2, 'Guard Razor SBD', 150, 2, 108, 101.70, 203.40, 0, 203.40, 18, 240.02),
        ]:
            for value, x, width in [
                (number, 5, 25), (name, 50, 260), (f'{mrp:.2f}', 500, 50),
                (0, 565, 25), (qty, 610, 30), (upc, 655, 40),
                (f'{rate:.2f}', 710, 65), (f'{gross:.2f}', 795, 65),
                (f'{scheme:.2f}', 875, 55), ('0.00', 950, 55),
                (f'{taxable:.2f}', 1030, 65), (f'{gst:.2f}', 1110, 45),
                (f'{net:.2f}', 1330, 65),
            ]:
                tokens.append(self._token(str(value), x, y, width))
        tokens.append(self._token('Total', 50, 340, 60))
        alias_token = local_bill_ocr._HEADER_ALIASES.set(aliases)
        template_token = local_bill_ocr._OCR_TEMPLATE.set({
            'strict': True, 'aliases': aliases, 'data_rows_per_item': 1,
        })
        try:
            parsed = local_bill_ocr._parse_page(tokens, 1450, 420)
        finally:
            local_bill_ocr._OCR_TEMPLATE.reset(template_token)
            local_bill_ocr._HEADER_ALIASES.reset(alias_token)
        self.assertEqual(len(parsed['lines']), 2, parsed['warnings'])
        self.assertEqual([line['quantity'] for line in parsed['lines']], [5, 2])
        self.assertEqual([line['purchase_rate'] for line in parsed['lines']], [18.28, 101.70])
        self.assertEqual([line['gst_percent'] for line in parsed['lines']], [5, 18])

    def test_taxable_only_goods_table_recovers_unit_rate(self):
        aliases = {
            'hsn': ['HSN Code'], 'description': ['Product Name & Desc'],
            'quantity': ['Quantity'], 'taxable': ['Taxable Amt'],
            'gst': ['Tax Rate (C+S)'],
        }
        tokens = [
            self._token('HSN Code', 10, 190, 85),
            self._token('Product Name & Desc', 160, 190, 230),
            self._token('Quantity', 1110, 190, 85),
            self._token('Taxable Amt', 1410, 190, 120),
            self._token('Tax Rate (C+S)', 1590, 190, 160),
        ]
        for y, name, quantity, amount in [
            (245, '5.00 Nice Jar (25 Pcs)', '12 PCS', '914.28'),
            (290, '5.00 Kadalai Mittai Jar (Pouch) - 60 Pcs', '6 PCS', '1,000.02'),
            (335, '1.00 Kadalai Mittai (20 Pcs)', '100 PAC', '1,333.00'),
        ]:
            tokens += [self._token('21069099', 10, y, 100),
                       self._token(name, 160, y, 700),
                       self._token(quantity, 1110, y, 100),
                       self._token(amount, 1410, y, 110),
                       self._token('2.50+2.50', 1590, y, 120)]
        tokens.append(self._token('Total Inv Amt', 1300, 390, 130))
        alias_token = local_bill_ocr._HEADER_ALIASES.set(aliases)
        template_token = local_bill_ocr._OCR_TEMPLATE.set({
            'strict': True, 'aliases': aliases, 'data_rows_per_item': 1,
        })
        try:
            parsed = local_bill_ocr._parse_page(tokens, 1800, 450)
        finally:
            local_bill_ocr._OCR_TEMPLATE.reset(template_token)
            local_bill_ocr._HEADER_ALIASES.reset(alias_token)
        self.assertEqual(len(parsed['lines']), 3, parsed['warnings'])
        self.assertEqual([line['quantity'] for line in parsed['lines']], [12, 6, 100])
        self.assertEqual([line['purchase_rate'] for line in parsed['lines']], [76.19, 166.67, 13.33])
        self.assertEqual([line['gst_percent'] for line in parsed['lines']], [5, 5, 5])

    def test_box_count_does_not_change_explicit_jar_quantity(self):
        aliases = {
            'serial': ['Sl No'], 'description': ['Description of Goods'],
            'hsn': ['HSN/SAC'], 'quantity': ['Quantity'],
            'tax_inclusive_rate': ['Rate (Incl. of Tax)', 'Net Rate'],
            'rate': ['Basic Rate per'], 'case': ['Boxes'], 'net': ['Amount'],
        }
        tokens = [
            self._token('Sl No', 10, 190, 45),
            self._token('Description of Goods', 80, 190, 220),
            self._token('HSN/SAC', 420, 190, 100),
            self._token('Quantity', 610, 190, 90),
            self._token('Rate (Incl. of Tax)', 760, 190, 160),
            self._token('Boxes', 930, 190, 70),
            self._token('Net Rate', 1010, 190, 90),
            self._token('Basic Rate per', 1170, 190, 130),
            self._token('Amount', 1350, 190, 90),
        ]
        for y, number, name, qty, inclusive, rate, amount in [
            (245, 1, '5.00 Nice Jar (25 Pcs)', '12 Jar', '80.00', '76.19', '914.28'),
            (290, 2, '5.00 Kadalai Mittai Jar (Pouch)', '6 Jar', '175.00', '166.67', '1,000.02'),
            (335, 3, '1.00 Kadalai Mittai (20 Pcs)', '100 Pkt', '14.00', '13.33', '1,333.00'),
        ]:
            tokens += [self._token(str(number), 10, y, 30),
                       self._token(name, 80, y, 300),
                       self._token('21069099', 420, y, 90),
                       self._token(qty, 610, y, 90),
                       self._token(inclusive, 760, y, 70),
                       self._token('1', 930, y, 25),
                       self._token(inclusive, 1010, y, 70),
                       self._token(rate, 1170, y, 70),
                       self._token(amount, 1350, y, 100)]
        tokens.append(self._token('Total', 80, 390, 70))
        alias_token = local_bill_ocr._HEADER_ALIASES.set(aliases)
        template_token = local_bill_ocr._OCR_TEMPLATE.set({
            'strict': True, 'aliases': aliases, 'data_rows_per_item': 1,
        })
        try:
            parsed = local_bill_ocr._parse_page(tokens, 1500, 450)
        finally:
            local_bill_ocr._OCR_TEMPLATE.reset(template_token)
            local_bill_ocr._HEADER_ALIASES.reset(alias_token)
        self.assertEqual(len(parsed['lines']), 3, parsed['warnings'])
        self.assertEqual([line['quantity'] for line in parsed['lines']], [12, 6, 100])
        self.assertEqual([line['purchase_rate'] for line in parsed['lines']], [76.19, 166.67, 13.33])
        self.assertEqual([line['gst_percent'] for line in parsed['lines']], [5, 5, 5])

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
        self.assertEqual(first["mrp"], 92.0)
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
