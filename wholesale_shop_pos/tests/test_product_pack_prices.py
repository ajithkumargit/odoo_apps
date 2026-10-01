from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase


class TestProductPackPrices(TransactionCase):
    def test_template_creation_and_edits_reach_variant(self):
        values = {'shop_box_price': 500, 'shop_box_qty': 20,
                  'shop_single_pack_price': 50, 'shop_single_pack_qty': 2}
        product = self.env['product.template'].create(dict(values, name='Pack Prices', list_price=25, standard_price=15))
        variant = product.product_variant_id
        for name, value in values.items():
            self.assertEqual(product[name], value)
            self.assertEqual(variant[name], value)
        product.shop_box_price = 550
        self.assertEqual(variant.shop_box_price, 550)
        self.assertEqual(variant.shop_box_qty, 20)
        variant.shop_single_pack_qty = 3
        self.assertEqual(product.shop_single_pack_qty, 3)
        self.assertEqual(product.list_price, 25)
        self.assertEqual(variant.standard_price, 15)

    def test_explicit_zero_and_existing_box_quantity(self):
        product = self.env['product.template'].create({
            'name': 'Zero quantities', 'shop_box_qty': 0, 'shop_single_pack_qty': 0,
        })
        self.assertEqual(product.product_variant_id.shop_box_qty, 0)
        self.assertEqual(product.product_variant_id.shop_single_pack_qty, 0)
        variant = self.env['product.product'].create({'name': 'Existing Box Quantity', 'shop_box_qty': 24})
        variant.shop_single_pack_price = 40
        self.assertEqual(variant.shop_box_qty, 24)
        self.assertEqual(variant.product_tmpl_id.shop_box_qty, 24)

    def test_variant_values_are_independent(self):
        attribute = self.env['product.attribute'].create({'name': 'Pack Size'})
        values = self.env['product.attribute.value'].create([
            {'name': 'Small', 'attribute_id': attribute.id},
            {'name': 'Large', 'attribute_id': attribute.id},
        ])
        template = self.env['product.template'].create({'name': 'Different packs',
            'attribute_line_ids': [(0, 0, {'attribute_id': attribute.id, 'value_ids': [(6, 0, values.ids)]})]})
        first, second = template.product_variant_ids.sorted('id')
        first.write({'shop_box_price': 400, 'shop_box_qty': 10, 'shop_single_pack_price': 40, 'shop_single_pack_qty': 1})
        second.write({'shop_box_price': 800, 'shop_box_qty': 20, 'shop_single_pack_price': 80, 'shop_single_pack_qty': 2})
        self.assertEqual(first.shop_box_price, 400)
        self.assertEqual(first.shop_box_qty, 10)
        self.assertEqual(first.shop_single_pack_price, 40)
        self.assertEqual(first.shop_single_pack_qty, 1)
        template.shop_box_price = 100
        third = self.env['product.attribute.value'].create({
            'name': 'Medium', 'attribute_id': attribute.id,
        })
        template.attribute_line_ids.write({'value_ids': [(4, third.id)]})
        self.assertEqual(len(template.product_variant_ids), 3)
        self.assertEqual(first.shop_box_price, 400)
        self.assertEqual(second.shop_box_price, 800)

    def test_negative_values_rejected(self):
        product = self.env['product.product'].create({'name': 'Check Pack Values'})
        for name in ('shop_box_price', 'shop_box_qty', 'shop_single_pack_price', 'shop_single_pack_qty'):
            with self.assertRaises(ValidationError), self.cr.savepoint():
                product[name] = -1

    def test_fields_visible_in_both_product_forms(self):
        from lxml import etree
        for model, xmlid in [
            ('product.template', 'product.product_template_only_form_view'),
            ('product.product', 'product.product_normal_form_view'),
            ('product.product', 'product.product_variant_easy_edit_view'),
        ]:
            arch = etree.fromstring(self.env[model].get_view(view_id=self.env.ref(xmlid).id, view_type='form')['arch'])
            for name in ('shop_box_price', 'shop_box_qty', 'shop_single_pack_price', 'shop_single_pack_qty'):
                self.assertEqual(len(arch.xpath("//field[@name='%s']" % name)), 1, (xmlid, name))
