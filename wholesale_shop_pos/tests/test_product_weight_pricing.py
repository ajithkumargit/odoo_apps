from odoo import Command
from odoo.tests.common import TransactionCase
from odoo.exceptions import ValidationError

from ..models.product_weight_pricing import grams_from_name


class TestProductWeightPricing(TransactionCase):
    def setUp(self):
        super().setUp()
        self.attribute = self.env['product.attribute'].create({'name': 'Pack weight'})
        self.weights = self.env['product.attribute.value'].create([
            {'name': name, 'attribute_id': self.attribute.id}
            for name in ['100g', '250g', '0.5kg', '750g', '1kg']
        ])
        self.template = self.env['product.template'].create({
            'name': 'Rice weight pricing', 'list_price': 120,
            'shop_price_by_weight': True, 'shop_cost_per_kg': 80,
            'attribute_line_ids': [Command.create({
                'attribute_id': self.attribute.id,
                'value_ids': [Command.set(self.weights.ids)],
            })],
        })

    def assert_prices(self, cost, sale):
        for variant in self.template.product_variant_ids:
            ratio = variant.shop_variant_weight_grams / 1000
            self.assertAlmostEqual(variant.standard_price, cost * ratio)
            self.assertAlmostEqual(variant.lst_price, variant.currency_id.round(sale * ratio))

    def test_create_and_pricelist(self):
        self.assertEqual(self.template.shop_weight_attribute_id, self.attribute)
        self.assertEqual(len(self.template.product_variant_ids), 5)
        self.assert_prices(80, 120)
        pricelist = self.env['product.pricelist'].create({'name': 'Weight test'})
        for variant in self.template.product_variant_ids:
            self.assertAlmostEqual(pricelist._get_product_price(variant, 1), variant.lst_price)

    def test_kg_updates_and_profit(self):
        self.template.shop_cost_per_kg = 100
        self.assert_prices(100, 120)
        self.template.shop_sale_price_per_kg = 200
        self.assert_prices(100, 200)
        self.template.shop_profit_percent = 25
        self.assert_prices(100, 125)
        self.template.list_price = 180
        self.template.shop_profit_percent = 25
        self.assert_prices(100, 180)

    def test_supplier_pack_cost(self):
        pack = self.template.product_variant_ids.filtered(lambda p: p.shop_variant_weight_grams == 250)
        pack.standard_price = 30
        self.assertEqual(self.template.shop_cost_per_kg, 120)
        self.assert_prices(120, 120)

    def test_weight_edit_and_new_variant(self):
        self.weights[1].name = '300g'
        self.assert_prices(80, 120)
        self.weights[1].shop_weight_grams = 125
        self.assert_prices(80, 120)
        value = self.env['product.attribute.value'].create({'name': '625g', 'attribute_id': self.attribute.id})
        self.template.attribute_line_ids.write({'value_ids': [Command.link(value.id)]})
        self.assertEqual(len(self.template.product_variant_ids), 6)
        self.assert_prices(80, 120)

    def test_validation_and_disable(self):
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.weights[0].shop_weight_grams = 0
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.template.product_variant_ids[0].lst_price = 23
        self.template.shop_price_by_weight = False
        self.template.product_variant_ids[0].lst_price = 23
        self.assertEqual(self.template.product_variant_ids[0].lst_price, 23)

    def test_weight_names(self):
        for name, expected in [('250 gm', 250), ('0.5 kg', 500), ('125g', 125), ('Large', 0)]:
            self.assertEqual(grams_from_name(name), expected)

    def test_configured_weights_are_dynamic_and_additive(self):
        params = self.env['ir.config_parameter'].sudo()
        params.set_param('wholesale_shop_pos.weight_options', '100g,200g,500g,750g,125g')
        self.template.action_load_shop_weight_options()
        self.assertEqual(len(self.template.product_variant_ids), 7)
        self.assert_prices(80, 120)
        self.template.action_load_shop_weight_options()
        self.assertEqual(len(self.template.product_variant_ids), 7)
        params.set_param('wholesale_shop_pos.weight_options', '625g')
        self.template.product_variant_ids[0].action_load_shop_weight_options()
        self.assertEqual(len(self.template.product_variant_ids), 8)
        self.assert_prices(80, 120)
        params.set_param('wholesale_shop_pos.weight_options', 'wrong,0g')
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.template.action_load_shop_weight_options()
