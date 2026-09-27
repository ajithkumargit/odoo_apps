import base64

from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase


class TestProductMRP(TransactionCase):
    def setUp(self):
        super().setUp()
        self.vendor = self.env['res.partner'].create({'name': 'MRP Test Vendor', 'shop_is_vendor': True})
        self.bill = self.env['shop.purchase.import'].create({
            'vendor_id': self.vendor.id,
            'original_file': base64.b64encode(b'image'), 'original_file_name': 'bill.jpg',
        })

    def test_product_template_and_variant_share_single_variant_mrp(self):
        template = self.env['product.template'].create({'name': 'MRP Product', 'shop_mrp': 120, 'list_price': 95})
        self.assertEqual(template.product_variant_id.shop_mrp, 120)
        template.product_variant_id.shop_mrp = 130
        self.assertEqual(template.shop_mrp, 130)
        template.shop_mrp = 140
        self.assertEqual(template.product_variant_id.shop_mrp, 140)
        self.assertEqual(template.list_price, 95)

    def test_variant_prices_are_independent(self):
        attribute = self.env['product.attribute'].create({'name': 'MRP Size'})
        values = self.env['product.attribute.value'].create([
            {'name': 'Small', 'attribute_id': attribute.id},
            {'name': 'Large', 'attribute_id': attribute.id},
        ])
        template = self.env['product.template'].create({
            'name': 'MRP variants',
            'attribute_line_ids': [(0, 0, {'attribute_id': attribute.id, 'value_ids': [(6, 0, values.ids)]})],
        })
        first, second = template.product_variant_ids.sorted('id')
        first.shop_mrp = 50
        second.shop_mrp = 100
        self.assertEqual(first.shop_mrp, 50)
        self.assertEqual(second.shop_mrp, 100)
        with self.assertRaises(ValidationError), self.cr.savepoint():
            template.shop_mrp = 80

    def test_extraction_and_new_product_keep_mrp_separate_from_cost(self):
        values = self.bill._line_values_from_extraction({
            'description': 'MRP Test Tea', 'quantity': 2, 'purchase_rate': 40, 'mrp': 65,
        }, 10)
        line = self.env['shop.purchase.import.line'].create(dict(values, import_id=self.bill.id))
        self.assertEqual(line.mrp, 65)
        total = line.total_amount
        line.mrp = 70
        self.assertEqual(line.total_amount, total)
        product = line._create_standalone_product()
        self.assertEqual(product.shop_mrp, 70)
        self.assertEqual(product.product_tmpl_id.shop_mrp, 70)
        self.assertEqual(product.standard_price, 40)

    def test_absent_mrp_does_not_overwrite_existing_product(self):
        product = self.env['product.product'].create({'name': 'Existing MRP', 'shop_mrp': 99})
        line = self.env['shop.purchase.import.line'].create({
            'import_id': self.bill.id, 'raw_description': 'Existing MRP', 'purchase_rate': 30,
        })
        line._attach_created_product(product)
        self.assertEqual(product.shop_mrp, 99)

    def test_negative_mrp_is_rejected(self):
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.env['product.product'].create({'name': 'Invalid MRP', 'shop_mrp': -1})
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.env['shop.purchase.import.line'].create({
                'import_id': self.bill.id, 'raw_description': 'Invalid MRP', 'mrp': -1,
            })
