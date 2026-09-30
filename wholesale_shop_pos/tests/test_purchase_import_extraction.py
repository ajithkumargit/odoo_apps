import base64
from io import BytesIO
import json
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase
from PIL import Image


class TestPurchaseImportExtraction(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Keep tax matching deterministic on databases that already have GST.
        # TransactionCase rolls this change back together with the fixtures.
        cls.env['account.tax'].search([
            ('company_id', '=', cls.env.company.id),
            ('type_tax_use', '=', 'purchase'),
        ]).write({'active': False})
        cls.vendor = cls.env["res.partner"].create({
            "name": "SNK Pillai Agencies",
            "shop_is_vendor": True,
            "vat": "32ABCDE1234F1Z5",
        })
        cls.product = cls.env["product.product"].create({
            "name": "Milk Biscuits",
            "purchase_ok": True,
            "barcode": "8901234567890",
        })
        india = cls.env.ref("base.in")
        cls.tax_group = cls.env["account.tax.group"].create({
            "name": "GST Purchase",
            "company_id": cls.env.company.id,
            "country_id": india.id,
        })
        cls.purchase_tax = cls.env["account.tax"].create({
            "name": "GST 18% Purchase",
            "amount": 18.0,
            "amount_type": "percent",
            "type_tax_use": "purchase",
            "company_id": cls.env.company.id,
            "country_id": india.id,
            "tax_group_id": cls.tax_group.id,
        })

    def _new_import(self):
        return self.env["shop.purchase.import"].create({
            "original_file": base64.b64encode(b"\x89PNG\r\n\x1a\nmock image"),
            "original_file_name": "bill.png",
        })

    def test_partner_roles_default_from_creation_view_context(self):
        vendor = self.env["res.partner"].with_context(
            default_shop_is_vendor=True
        ).create({"name": "Context Vendor"})
        customer = self.env["res.partner"].with_context(
            default_shop_is_customer=True
        ).create({"name": "Context Customer"})

        self.assertTrue(vendor.shop_is_vendor)
        self.assertFalse(vendor.shop_is_customer)
        self.assertTrue(customer.shop_is_customer)
        self.assertFalse(customer.shop_is_vendor)

    def test_prepare_image_file(self):
        purchase_import = self._new_import()

        file_bytes, mimetype = purchase_import._prepare_bill_file()

        self.assertEqual(file_bytes, b"\x89PNG\r\n\x1a\nmock image")
        self.assertEqual(mimetype, "image/png")

    def test_prepare_file_applies_manual_crop_without_changing_original(self):
        source = Image.new("RGB", (100, 80), "white")
        output = BytesIO()
        source.save(output, format="PNG")
        original = output.getvalue()
        purchase_import = self.env["shop.purchase.import"].create({
            "original_file": base64.b64encode(original),
            "original_file_name": "bill.png",
            "manual_crop_enabled": True,
            "crop_left": 10, "crop_top": 25,
            "crop_right": 70, "crop_bottom": 75,
        })

        cropped, mimetype = purchase_import._prepare_bill_file()

        self.assertEqual(mimetype, "image/png")
        with Image.open(BytesIO(cropped)) as image:
            self.assertEqual(image.size, (60, 40))
        self.assertEqual(base64.b64decode(purchase_import.original_file), original)

    def test_multiple_bill_images_are_cropped_and_combined_in_order(self):
        first = Image.new("RGB", (100, 80), "white")
        first_output = BytesIO()
        first.save(first_output, format="PNG")
        second = Image.new("RGB", (120, 90), "white")
        second_output = BytesIO()
        second.save(second_output, format="PNG")
        purchase_import = self.env["shop.purchase.import"].create({
            "original_file": base64.b64encode(first_output.getvalue()),
            "original_file_name": "page-1.png",
            "page_ids": [(0, 0, {
                "sequence": 20,
                "page_file": base64.b64encode(second_output.getvalue()),
                "page_file_name": "page-2.png",
                "manual_crop_enabled": True,
                "crop_left": 0,
                "crop_top": 0,
                "crop_right": 50,
                "crop_bottom": 100,
            })],
        })

        documents = purchase_import._prepare_bill_documents()
        self.assertEqual(len(documents), 2)
        with Image.open(BytesIO(documents[1][0])) as cropped:
            self.assertEqual(cropped.size, (60, 90))

        combined, mimetype = purchase_import._prepare_combined_bill()
        self.assertEqual(mimetype, "application/pdf")
        import fitz
        with fitz.open(stream=combined, filetype="pdf") as document:
            self.assertEqual(document.page_count, 2)

    def test_manual_crop_rejects_pdf(self):
        purchase_import = self.env["shop.purchase.import"].create({
            "original_file": base64.b64encode(b"%PDF-1.4 mock"),
            "original_file_name": "bill.pdf",
            "manual_crop_enabled": True,
        })
        with self.assertRaisesRegex(UserError, "only for image bills"):
            purchase_import._prepare_bill_file()

    @patch("odoo.addons.wholesale_shop_pos.models.purchase_import.extract_bill")
    def test_local_extraction_uses_offline_service(self, mock_extract_bill):
        extracted_data = {
            "vendor_name": "SNK Pillai Agencies",
            "vendor_tax_id": None,
            "bill_number": "SNK-1001",
            "bill_date": "2026-09-04",
            "currency_code": "INR",
            "notes": None,
            "lines": [],
        }
        mock_extract_bill.return_value = extracted_data, "local-test-engine", "abc123"
        purchase_import = self._new_import()
        purchase_import.vendor_id = self.vendor

        result, engine, fingerprint = purchase_import._extract_bill_locally(
            *purchase_import._prepare_bill_file()
        )

        self.assertEqual(result, extracted_data)
        self.assertEqual(engine, "local-test-engine")
        self.assertEqual(fingerprint, "abc123")
        args, kwargs = mock_extract_bill.call_args
        self.assertEqual(args, (b"\x89PNG\r\n\x1a\nmock image", "image/png"))
        self.assertTrue(kwargs["template_config"]["strict"])
        self.assertEqual(kwargs["template_config"]["vendor_name"], self.vendor.name)

    def test_vendor_template_aliases_are_stored_and_used(self):
        purchase_import = self.env["shop.purchase.import"].create({
            "vendor_id": self.vendor.id,
            "original_file": base64.b64encode(b"\x89PNG\r\n\x1a\nmock image"),
            "original_file_name": "bill.png",
        })
        template = self.env["shop.bill.ocr.template"].search([
            ("company_id", "=", self.env.company.id),
            ("partner_id", "=", self.vendor.id),
        ])
        self.assertEqual(len(template), 1)
        template.data_rows_per_item = 2
        template.alias_ids = [(0, 0, {
            "role": "description", "printed_label": "Article Details",
        })]
        self.assertEqual(
            template.config_key,
            "wholesale_shop_pos.ocr_template.%s" % template.id,
        )
        stored = json.loads(self.env["ir.config_parameter"].sudo().get_param(
            template.config_key
        ))
        self.assertIn("Article Details", stored["aliases"]["description"])
        self.assertEqual(stored["data_rows_per_item"], 2)
        aliases = template.get_extraction_config()["aliases"]
        self.assertIn("Article Details", aliases["description"])

        with patch(
            "odoo.addons.wholesale_shop_pos.models.purchase_import.extract_bill",
            return_value=({}, "test", "fingerprint"),
        ) as mocked:
            purchase_import._extract_bill_locally(*purchase_import._prepare_bill_file())
        self.assertEqual(
            mocked.call_args.kwargs["template_config"]["aliases"]["description"],
            aliases["description"],
        )
        self.assertEqual(
            mocked.call_args.kwargs["template_config"]["data_rows_per_item"], 2
        )

    def test_multiple_vendor_bill_types_require_template_selection(self):
        first = self.env["shop.bill.ocr.template"].ensure_for_vendor(
            self.env.company, self.vendor
        )[0]
        second = self.env["shop.bill.ocr.template"].create({
            "name": "Retail Invoice",
            "template_code": "retail",
            "company_id": self.env.company.id,
            "partner_id": self.vendor.id,
        })
        purchase_import = self.env["shop.purchase.import"].create({
            "vendor_id": self.vendor.id,
            "original_file": base64.b64encode(b"\x89PNG\r\n\x1a\nmock image"),
            "original_file_name": "bill.png",
        })
        self.assertFalse(purchase_import.ocr_template_id)
        with self.assertRaisesRegex(UserError, "multiple bill layouts"):
            purchase_import._extract_bill_locally(*purchase_import._prepare_bill_file())
        purchase_import.ocr_template_id = second
        with patch(
            "odoo.addons.wholesale_shop_pos.models.purchase_import.extract_bill",
            return_value=({}, "test", "fingerprint"),
        ) as mocked:
            purchase_import._extract_bill_locally(*purchase_import._prepare_bill_file())
        self.assertEqual(mocked.call_args.kwargs["template_config"]["template_id"], second.id)
        self.assertNotEqual(first.id, second.id)

    def test_matched_product_gets_vendor_price_and_standard_cost(self):
        product = self.env["product.product"].create({
            "name": "Mapped Vendor Product", "purchase_ok": True,
        })
        purchase_import = self.env["shop.purchase.import"].create({
            "vendor_id": self.vendor.id,
            "bill_date": "2026-09-15",
            "state": "review",
            "line_ids": [(0, 0, {
                "raw_description": "Vendor Short Product",
                "product_id": product.id,
                "quantity": 4,
                "purchase_rate": 80,
                "discount_percent": 10,
            })],
        })
        supplier = self.env["product.supplierinfo"].search([
            ("partner_id", "=", self.vendor.id), ("product_id", "=", product.id),
        ])
        self.assertEqual(len(supplier), 1)
        self.assertEqual((supplier.product_name, supplier.price, supplier.discount),
                         ("Vendor Short Product", 80, 10))

        purchase_import.action_create_reviewed_purchase_order()
        self.assertEqual(purchase_import.state, "po_created")
        self.assertEqual(
            purchase_import.purchase_order_id.shop_purchase_import_id,
            purchase_import,
        )
        self.assertAlmostEqual(product.with_company(self.env.company).standard_price, 72)

    def test_apply_extraction_matches_existing_records(self):
        purchase_import = self._new_import()
        extracted_data = {
            "vendor_name": "SNK Pillai Agencies",
            "vendor_tax_id": "32ABCDE1234F1Z5",
            "bill_number": "SNK-1001",
            "bill_date": "2026-09-04",
            "currency_code": self.env.company.currency_id.name,
            "notes": None,
            "lines": [
                {
                    "description": "Milk Biscuits",
                    "vendor_product_code": "MB-10",
                    "barcode": "8901234567890",
                    "hsn_code": "1905",
                    "quantity": 20,
                    "free_quantity": 2,
                    "purchase_rate": 8.5,
                    "discount_percent": 5,
                    "gst_percent": 18,
                    "confidence": 97,
                }
            ],
        }

        purchase_import._apply_extracted_bill(extracted_data, "test-model", "resp_test")

        self.assertEqual(purchase_import.vendor_id, self.vendor)
        self.assertEqual(purchase_import.state, "review")
        self.assertEqual(purchase_import.bill_number, "SNK-1001")
        self.assertEqual(len(purchase_import.line_ids), 1)
        line = purchase_import.line_ids
        self.assertEqual(line.product_id, self.product)
        self.assertEqual(line.tax_ids, self.purchase_tax)
        self.assertEqual(line.free_quantity, 2)
        self.assertEqual(line.gst_percent, 18)
        self.assertEqual(json.loads(purchase_import.raw_extracted_text), extracted_data)

    def test_bill_product_name_mapping_is_saved_and_reused(self):
        first_import = self._new_import()
        first_line = self.env["shop.purchase.import.line"].create({
            "import_id": first_import.id,
            "raw_description": "HLX PLAIN BIS 5/-",
            "corrected_description": "Horlicks Plain Biscuits",
        })

        first_line.product_id = self.product

        mapping = self.env["shop.bill.product.name.map"].search([
            ("company_id", "=", self.env.company.id),
            ("normalized_bill_product_name", "=", "HLX PLAIN BIS 5/-"),
        ])
        self.assertEqual(mapping.product_id, self.product)

        second_import = self._new_import()
        second_line = self.env["shop.purchase.import.line"].create({
            "import_id": second_import.id,
            "raw_description": "  hlx plain bis 5/-  ",
        })

        second_import.action_match_products()

        self.assertEqual(second_line.product_id, self.product)
        self.assertEqual(second_line.corrected_description, self.product.name)

    def test_variant_cost_uses_tax_inclusive_cost_per_item(self):
        purchase_import = self.env["shop.purchase.import"].create({
            "vendor_id": self.vendor.id,
            "state": "review",
            "line_ids": [(0, 0, {
                "raw_description": "Pack of Milk Biscuits",
                "product_id": self.product.id,
                "quantity": 2,
                "purchase_rate": 100,
                "discount_percent": 10,
                "units_per_purchase_qty": 10,
                "tax_ids": [(6, 0, self.purchase_tax.ids)],
            })],
        })
        line = purchase_import.line_ids
        self.assertAlmostEqual(line.item_unit_cost_incl_tax, 10.62)
        line._sync_product_cost(self.product)
        self.assertAlmostEqual(self.product.with_company(line.company_id).standard_price, 10.62)
        self.assertAlmostEqual(line.purchase_rate, 100)
        self.assertAlmostEqual(line._effective_purchase_price(), 90)
        line.purchase_rate = 0
        line._sync_product_cost(self.product)
        self.assertAlmostEqual(self.product.with_company(line.company_id).standard_price, 10.62)

    def test_imported_bag_uses_base_quantity_and_unit_price(self):
        self.product.uom_id = self.env.ref('uom.product_uom_kgm')
        bill = self.env['shop.purchase.import'].create({
            'vendor_id': self.vendor.id, 'state': 'ready',
            'line_ids': [(0, 0, {'product_id': self.product.id, 'raw_description': 'Sugar bag',
                'quantity': 1, 'purchase_rate': 2450, 'units_per_purchase_qty': 50,
                'uom_id': self.product.uom_id.id, 'tax_ids': [(5, 0, 0)]})],
        })
        bill.action_create_purchase_order()
        line = bill.purchase_order_id.order_line
        self.assertEqual(line.product_qty, 50)
        self.assertEqual(line.price_unit, 49)
        self.assertEqual(line.price_total, 2450)
        self.assertEqual(self.product.standard_price, 49)

    def test_edit_total_updates_rate_and_preserves_reviewed_inputs(self):
        purchase_import = self.env["shop.purchase.import"].create({
            "vendor_id": self.vendor.id,
            "state": "review",
            "line_ids": [(0, 0, {
                "raw_description": "Editable total product",
                "product_id": self.product.id,
                "quantity": 48,
                "discount_percent": 10,
                "purchase_rate": 10,
                "tax_ids": [(6, 0, self.purchase_tax.ids)],
            })],
        })
        line = purchase_import.line_ids
        line.total_amount = 500
        self.assertAlmostEqual(line.total_amount, 500, places=2)
        self.assertAlmostEqual(purchase_import.total_amount, 500, places=2)
        self.assertEqual(line.quantity, 48)
        self.assertEqual(line.discount_percent, 10)
        self.assertEqual(line.tax_ids, self.purchase_tax)
        line.tax_ids = False
        line.total_amount = 400
        self.assertAlmostEqual(line.total_amount, 400, places=2)
        line.quantity = 0
        with self.assertRaisesRegex(UserError, "positive quantity"):
            line.total_amount = 100

    def test_reviewed_po_preserves_selected_tax_over_ocr_rate(self):
        purchase_import = self.env["shop.purchase.import"].create({
            "vendor_id": self.vendor.id,
            "state": "review",
            "line_ids": [(0, 0, {
                "raw_description": "Reviewed Milk Biscuits",
                "product_id": self.product.id,
                "quantity": 1,
                "purchase_rate": 100,
                "gst_percent": 3,
                "tax_ids": [(6, 0, self.purchase_tax.ids)],
            })],
        })
        purchase_import.action_create_reviewed_purchase_order()
        self.assertEqual(purchase_import.line_ids.tax_ids, self.purchase_tax)
        self.assertEqual(purchase_import.purchase_order_id.order_line.tax_ids, self.purchase_tax)

    def test_missing_tax_validation_identifies_line(self):
        purchase_import = self.env["shop.purchase.import"].create({
            "vendor_id": self.vendor.id,
            "line_ids": [(0, 0, {
                "raw_description": "Unmatched GST item",
                "product_id": self.product.id,
                "quantity": 1,
                "purchase_rate": 100,
                "gst_percent": 3,
                "tax_ids": [(5, 0, 0)],
            })],
        })
        with self.assertRaisesRegex(UserError, "Unmatched GST item"):
            purchase_import.action_mark_ready()

    def test_five_percent_does_not_partially_match_fifteen_percent_tax(self):
        fifteen_percent_tax = self.env["account.tax"].create({
            "name": "15%",
            "amount": 15.0,
            "amount_type": "percent",
            "type_tax_use": "purchase",
            "company_id": self.env.company.id,
            "country_id": self.env.ref("base.in").id,
            "tax_group_id": self.purchase_tax.tax_group_id.id,
        })
        purchase_import = self._new_import()
        purchase_import.line_ids = [(0, 0, {
            "raw_description": "Milk Biscuits",
            "gst_percent": 5.0,
            "tax_ids": [(6, 0, fifteen_percent_tax.ids)],
        })]

        purchase_import.action_match_taxes()

        self.assertFalse(purchase_import.line_ids.tax_ids)

    def test_gst_group_matches_sum_of_component_rates(self):
        component_taxes = self.env["account.tax"].create([
            {
                "name": "CGST 2.5%",
                "amount": 2.5,
                "amount_type": "percent",
                "type_tax_use": "none",
                "company_id": self.env.company.id,
                "country_id": self.env.ref("base.in").id,
                "tax_group_id": self.tax_group.id,
            },
            {
                "name": "SGST 2.5%",
                "amount": 2.5,
                "amount_type": "percent",
                "type_tax_use": "none",
                "company_id": self.env.company.id,
                "country_id": self.env.ref("base.in").id,
                "tax_group_id": self.tax_group.id,
            },
        ])
        gst_group = self.env["account.tax"].create({
            "name": "GST 5% Purchase",
            "amount_type": "group",
            "type_tax_use": "purchase",
            "company_id": self.env.company.id,
            "country_id": self.env.ref("base.in").id,
            "tax_group_id": self.tax_group.id,
            "children_tax_ids": [(6, 0, component_taxes.ids)],
        })
        purchase_import = self._new_import()

        self.assertEqual(purchase_import._find_purchase_tax(5.0), gst_group)

    def test_create_missing_product_sets_cost_hsn_and_vendor_price(self):
        purchase_import = self._new_import()
        purchase_import.write({
            "vendor_id": self.vendor.id,
            "bill_date": "2026-09-04",
            "state": "review",
            "line_ids": [(0, 0, {
                "raw_description": "New Coffee Jar 200G",
                "vendor_product_code": "COF-200",
                "barcode": "8901234500200",
                "hsn_code": "21011120",
                "quantity": 2,
                "purchase_rate": 100.0,
                "discount_percent": 10.0,
                "units_per_purchase_qty": 10.0,
                "gst_percent": 18.0,
                "tax_ids": [(6, 0, self.purchase_tax.ids)],
            })],
        })

        purchase_import.action_create_missing_products()

        product = purchase_import.line_ids.product_id
        self.assertTrue(product)
        self.assertTrue(product.product_tmpl_id)
        self.assertTrue(product.is_storable)
        self.assertTrue(product.available_in_pos)
        self.assertEqual(product.barcode, "8901234500200")
        self.assertEqual(product.default_code, "COF-200")
        self.assertEqual(product.shop_hsn_code, "21011120")
        self.assertAlmostEqual(product.standard_price, 10.62)
        self.assertAlmostEqual(purchase_import.line_ids.total_amount, 212.4)
        self.assertAlmostEqual(
            purchase_import.line_ids.purchase_unit_price_incl_tax, 106.2
        )
        self.assertAlmostEqual(
            purchase_import.line_ids.item_unit_cost_incl_tax, 10.62
        )
        self.assertAlmostEqual(purchase_import.line_ids.purchase_rate, 100.0)
        supplierinfo = self.env["product.supplierinfo"].search([
            ("partner_id", "=", self.vendor.id),
            ("product_id", "=", product.id),
        ])
        self.assertEqual(len(supplierinfo), 1)
        self.assertEqual(supplierinfo.price, 100.0)
        self.assertEqual(supplierinfo.discount, 10.0)
        mapping = self.env["shop.vendor.product.map"].search([
            ("partner_id", "=", self.vendor.id),
            ("vendor_product_name", "=ilike", "New Coffee Jar 200G"),
        ])
        self.assertEqual(mapping.product_id, product)

        purchase_import.action_mark_ready()
        purchase_import.action_create_purchase_order()
        self.assertEqual(purchase_import.purchase_order_id.order_line.product_id, product)
        self.assertEqual(
            purchase_import.purchase_order_id.order_line.product_uom_id,
            product.uom_id,
        )

    def test_create_variants_from_bill_lines(self):
        purchase_import = self._new_import()
        purchase_import.write({
            "vendor_id": self.vendor.id,
            "state": "review",
            "line_ids": [
                (0, 0, {
                    "raw_description": "Coffee 100G",
                    "purchase_rate": 50.0,
                }),
                (0, 0, {
                    "raw_description": "Coffee 200G",
                    "purchase_rate": 80.0,
                }),
            ],
        })
        first_line, second_line = purchase_import.line_ids.sorted("sequence")
        unit = self.env.ref("uom.product_uom_unit")
        Wizard = self.env["shop.purchase.import.create.product.wizard"]

        Wizard.create({
            "line_id": first_line.id,
            "vendor_id": self.vendor.id,
            "creation_mode": "new_variant",
            "product_name": "Coffee",
            "variant_spec": "Size: 100G",
            "standard_price": 50.0,
            "uom_id": unit.id,
            "box_quantity": 1.0,
        }).action_create_product()
        template = first_line.product_id.product_tmpl_id

        Wizard.create({
            "line_id": second_line.id,
            "vendor_id": self.vendor.id,
            "creation_mode": "existing_variant",
            "product_name": "Coffee",
            "product_tmpl_id": template.id,
            "variant_spec": "Size: 200G",
            "standard_price": 80.0,
            "uom_id": unit.id,
            "box_quantity": 1.0,
        }).action_create_product()

        self.assertEqual(first_line.product_id.product_tmpl_id, template)
        self.assertEqual(second_line.product_id.product_tmpl_id, template)
        self.assertNotEqual(first_line.product_id, second_line.product_id)
        self.assertEqual(template.product_variant_count, 2)
        self.assertTrue(template.available_in_pos)
        self.assertEqual(
            second_line.product_id.product_template_attribute_value_ids
            .product_attribute_value_id.name,
            "200G",
        )
        self.assertAlmostEqual(second_line.product_id.standard_price, 80.0)

    def test_purchase_order_integrated_ocr_receive_and_draft_bill(self):
        self._check_integrated_purchase_revert(post_bill=False)

    def test_purchase_order_revert_cancels_posted_bill(self):
        self._check_integrated_purchase_revert(post_bill=True)

    def _check_integrated_purchase_revert(self, post_bill):
        stock_product = self.env["product.product"].create({
            "name": "Integrated OCR Product",
            "purchase_ok": True,
            "sale_ok": True,
            "is_storable": True,
            "available_in_pos": True,
        })
        order = self.env["purchase.order"].create({
            "partner_id": self.vendor.id,
            "currency_id": self.env.company.currency_id.id,
        })
        purchase_import = self._new_import()
        purchase_import.write({
            "vendor_id": self.vendor.id,
            "bill_number": "OCR-PO-1001",
            "bill_date": "2026-09-05",
            "purchase_order_id": order.id,
            "state": "review",
            "line_ids": [(0, 0, {
                "raw_description": "Integrated OCR Product",
                "corrected_description": "OCR Product",
                "product_id": stock_product.id,
                "quantity": 3.0,
                "purchase_rate": 40.0,
            })],
        })
        order.shop_purchase_import_id = purchase_import
        quantity_before = stock_product.qty_available

        order.action_shop_apply_ocr_lines()

        self.assertEqual(len(order.order_line), 1)
        self.assertEqual(order.order_line.product_id, stock_product)
        self.assertEqual(purchase_import.state, "po_created")
        self.assertEqual(order.partner_ref, "OCR-PO-1001")

        order.action_shop_receive_and_create_bill()

        self.assertEqual(order.state, "purchase")
        self.assertTrue(order.picking_ids)
        self.assertTrue(all(state == "done" for state in order.picking_ids.mapped("state")))
        self.assertEqual(len(order.invoice_ids), 1)
        self.assertEqual(order.invoice_ids.state, "draft")
        self.assertEqual(order.invoice_ids.ref, "OCR-PO-1001")
        self.assertEqual(stock_product.qty_available - quantity_before, 3)

        if post_bill:
            order.invoice_ids.action_post()
            self.assertEqual(order.invoice_ids.state, 'posted')
            self.assertEqual(order.invoice_ids.payment_state, 'not_paid')

        order.action_shop_revert_import()

        self.assertEqual(order.state, "cancel")
        self.assertTrue(all(bill.state == "cancel" for bill in order.invoice_ids))
        self.assertEqual(stock_product.qty_available, quantity_before)
        self.assertEqual(purchase_import.state, "review")
        self.assertFalse(purchase_import.purchase_order_id)
        self.assertFalse(order.shop_purchase_import_id)

        # Reimport the corrected bag cost after reversing the original receipt.
        purchase_import.line_ids.write({
            'purchase_rate': 3050.10, 'units_per_purchase_qty': 30,
        })
        self.assertAlmostEqual(purchase_import.line_ids.item_unit_cost_incl_tax, 101.67)
        self.assertAlmostEqual(stock_product.standard_price, 101.67)
        purchase_import.action_create_reviewed_purchase_order()
        replacement = purchase_import.purchase_order_id
        self.assertNotEqual(replacement, order)
        self.assertAlmostEqual(stock_product.standard_price, 101.67)
        replacement.action_shop_receive_and_create_bill()
        self.assertAlmostEqual(stock_product.standard_price, 101.67)

    def test_reextraction_and_review_corrections_update_matched_cost(self):
        self.product.standard_price = 80
        self.product.lst_price = 140
        purchase_import = self._new_import()
        purchase_import._apply_extracted_bill({
            'lines': [{'description': self.product.name, 'barcode': self.product.barcode,
                       'quantity': 1, 'purchase_rate': 101.67}],
        }, 'test', 'cost-reimport')
        self.assertEqual(purchase_import.state, 'review')
        self.assertFalse(purchase_import.purchase_order_id)
        self.assertEqual(purchase_import.line_ids.product_id, self.product)
        self.assertAlmostEqual(self.product.standard_price, 101.67)
        self.assertEqual(self.product.lst_price, 140)
        purchase_import.line_ids.write({'purchase_rate': 3050.10, 'units_per_purchase_qty': 30})
        self.assertAlmostEqual(self.product.standard_price, 101.67)
        purchase_import.line_ids.purchase_rate = 0
        self.assertAlmostEqual(self.product.standard_price, 101.67)

    def test_ready_requires_vendor(self):
        purchase_import = self._new_import()
        purchase_import.line_ids = [(0, 0, {
            "raw_description": "Milk Biscuits",
            "product_id": self.product.id,
            "quantity": 1,
            "purchase_rate": 8.5,
        })]
        purchase_import.state = "review"

        with self.assertRaises(UserError):
            purchase_import.action_mark_ready()

    def test_missing_vendor_is_handled_before_mapping(self):
        purchase_import = self._new_import()
        purchase_import.write({
            "state": "review",
            "line_ids": [(0, 0, {
                "raw_description": "Unassigned Vendor Product",
                "quantity": 1,
                "purchase_rate": 10.0,
            })],
        })
        line = purchase_import.line_ids

        action = line.action_open_create_product()
        self.assertFalse(action["context"]["default_vendor_id"])
        with self.assertRaisesRegex(UserError, "Select the vendor"):
            purchase_import.action_create_missing_products()
