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
        self.template.product_variant_ids[0].lst_price = 23
        self.assertEqual(self.template.product_variant_ids[0].lst_price, 23)
        self.template.shop_price_by_weight = False
        self.template.product_variant_ids[0].lst_price = 23
        self.assertEqual(self.template.product_variant_ids[0].lst_price, 23)

    def test_weight_names(self):
        for name, expected in [('250 gm', 250), ('0.5 kg', 500), ('125g', 125), ('Large', 0)]:
            self.assertEqual(grams_from_name(name), expected)

    def test_variant_form_saves_weight_settings_atomically(self):
        self.template.shop_price_by_weight = False
        self.template.shop_cost_per_kg = 0
        variant = self.template.product_variant_ids.filtered(lambda p: p.shop_variant_weight_grams == 250)
        variant.write({
            'shop_price_by_weight': True,
            'shop_weight_attribute_id': self.attribute.id,
            'shop_cost_per_kg': 46.15,
            'shop_sale_price_per_kg': 52,
            'standard_price': 46.15,
        })
        self.assertAlmostEqual(self.template.shop_cost_per_kg, 46.15)
        self.assertAlmostEqual(variant.standard_price, 46.15 / 4)
        self.assertAlmostEqual(variant.lst_price, 13)
        variant.write({'shop_weight_sale_amount': 15, 'shop_cost_per_kg': 48})
        self.assertAlmostEqual(variant.standard_price, 12)
        self.assertAlmostEqual(variant.lst_price, 15)

    def test_edit_cost_and_sale_fields_without_readonly_guards(self):
        self.template.uom_id = self.env.ref('uom.product_uom_kgm')
        variant = self.template.product_variant_ids.filtered(lambda p: p.shop_variant_weight_grams == 250)
        self.template.standard_price = 51
        self.assertEqual(self.template.standard_price, 51)
        variant.standard_price = 52
        self.assertEqual(self.template.shop_cost_per_kg, 52)
        variant.shop_weight_cost_amount = 12.75
        self.assertEqual(self.template.shop_cost_per_kg, 51)
        variant.lst_price = 56
        self.assertEqual(variant.shop_weight_sale_amount, 14)
        variant.shop_variant_sale_price = 60
        self.assertEqual(variant.shop_weight_sale_amount, 15)
        variant.write({'shop_variant_profit_percent': 30, 'lst_price': 64})
        self.assertEqual(variant.lst_price, 64)
        self.template.shop_cost_per_kg = 55
        self.assertEqual(variant.lst_price, 64)

    def test_restore_kg_cost_from_bill_for_original_archived_variant(self):
        template = self.env['product.template'].create({
            'name': 'Bulk sugar', 'uom_id': self.env.ref('uom.product_uom_kgm').id,
            'standard_price': 51, 'list_price': 56,
        })
        original = template.product_variant_id
        vendor = self.env['res.partner'].create({'name': 'Weight supplier'})
        order = self.env['purchase.order'].create({'partner_id': vendor.id})
        bill = self.env['shop.purchase.import'].create({
            'vendor_id': vendor.id, 'state': 'po_created', 'purchase_order_id': order.id,
        })
        line = self.env['shop.purchase.import.line'].create({
            'import_id': bill.id, 'product_id': original.id, 'quantity': 3,
            'raw_description': 'Sugar 50kg bag',
            'purchase_rate': 2550, 'units_per_purchase_qty': 50,
        })
        template.write({'attribute_line_ids': [Command.create({
            'attribute_id': self.attribute.id, 'value_ids': [Command.set(self.weights.ids)],
        })]})
        template.write({'shop_price_by_weight': True})
        self.assertEqual(template.shop_cost_per_kg, 51)
        for variant in template.product_variant_ids:
            self.assertEqual(variant.standard_price, 51)
        template.shop_cost_per_kg = 60
        template.action_update_cost_from_bill()
        self.assertEqual(template.shop_cost_per_kg, 51)
        self.assertEqual(template.list_price, 56)
        line.purchase_rate = 0
        line._sync_product_cost(original)
        self.assertEqual(template.shop_cost_per_kg, 51)

    def test_pack_restore_uses_its_own_bill_line(self):
        vendor = self.env['res.partner'].create({'name': 'Pack supplier'})
        order = self.env['purchase.order'].create({'partner_id': vendor.id})
        bill = self.env['shop.purchase.import'].create({
            'vendor_id': vendor.id, 'state': 'po_created', 'purchase_order_id': order.id,
        })
        pack = self.template.product_variant_ids.filtered(lambda p: p.shop_variant_weight_grams == 250)
        other = self.template.product_variant_ids.filtered(lambda p: p.shop_variant_weight_grams == 500)
        self.env['shop.purchase.import.line'].create([
            {'import_id': bill.id, 'raw_description': '250g pack', 'product_id': pack.id,
             'quantity': 1, 'purchase_rate': 20},
            {'import_id': bill.id, 'raw_description': '500g pack', 'product_id': other.id,
             'quantity': 1, 'purchase_rate': 60},
        ])
        self.template.shop_cost_per_kg = 100
        pack.action_update_cost_from_bill()
        self.assertEqual(pack.standard_price, 20)
        self.assertEqual(self.template.shop_cost_per_kg, 80)

    def test_weight_price_and_profit_controls(self):
        self.template.uom_id = self.env.ref('uom.product_uom_kgm')
        self.template.shop_cost_per_kg = 51
        variant = self.template.product_variant_ids.filtered(lambda p: p.shop_variant_weight_grams == 250)
        variant.shop_weight_sale_amount = 14
        self.assertAlmostEqual(self.template.list_price, 56)
        self.assertAlmostEqual(variant.shop_weight_sale_amount, 14)
        variant.shop_weight_profit_percent = 20
        self.assertAlmostEqual(self.template.list_price, 61.2)
        variant.shop_variant_price_check = 15
        self.assertAlmostEqual(variant.shop_variant_price_check_profit_percent, (15 - 12.75) / 12.75 * 100, places=6)
        variant.action_apply_shop_variant_checked_profit()
        self.assertAlmostEqual(variant.shop_weight_sale_amount, 15)
        self.assertAlmostEqual(self.template.list_price, 60)
        self.template.shop_cost_per_kg = 52
        self.assertAlmostEqual(variant.shop_weight_sale_amount, 15)

    def test_loose_weight_keeps_cost_and_price_per_kg(self):
        self.template.uom_id = self.env.ref('uom.product_uom_kgm')
        for variant in self.template.product_variant_ids:
            self.assertTrue(variant.shop_loose_weight)
            self.assertEqual(variant.standard_price, 80)
            self.assertEqual(variant.lst_price, 120)
        self.template.product_variant_ids[0].standard_price = 51
        self.assertEqual(self.template.shop_cost_per_kg, 51)
        self.template.shop_sale_price_per_kg = 56
        for variant in self.template.product_variant_ids:
            self.assertEqual(variant.standard_price, 51)
            self.assertEqual(variant.lst_price, 56)
            self.assertAlmostEqual(variant.shop_weight_sale_amount, 56 * variant.shop_variant_weight_grams / 1000)
            self.assertAlmostEqual(variant.shop_weight_cost_amount, 51 * variant.shop_variant_weight_grams / 1000)
        self.template.shop_sale_price_per_kg = 60
        for variant in self.template.product_variant_ids:
            self.assertAlmostEqual(variant.shop_weight_sale_amount, 60 * variant.shop_variant_weight_grams / 1000)

    def test_adding_weights_preserves_original_cost_in_same_uom(self):
        product = self.env['product.template'].create({
            'name': 'Sugar in kg', 'standard_price': 51, 'list_price': 56,
            'uom_id': self.env.ref('uom.product_uom_kgm').id,
        })
        product.write({'attribute_line_ids': [Command.create({
            'attribute_id': self.attribute.id,
            'value_ids': [Command.set(self.weights.ids)],
        })]})
        self.assertEqual(len(product.product_variant_ids), 5)
        for variant in product.product_variant_ids:
            self.assertEqual(variant.standard_price, 51)
            self.assertEqual(variant.lst_price, 56)
            self.assertEqual(variant.uom_id, self.env.ref('uom.product_uom_kgm'))

    def test_blank_kg_cost_cannot_erase_existing_costs(self):
        self.template.shop_price_by_weight = False
        self.template.shop_cost_per_kg = 0
        before = {p.id: p.standard_price for p in self.template.product_variant_ids}
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.template.shop_price_by_weight = True
        self.assertFalse(self.template.shop_price_by_weight)
        self.assertEqual(before, {p.id: p.standard_price for p in self.template.product_variant_ids})

    def test_zero_kg_or_supplier_cost_rolls_back(self):
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.template.shop_cost_per_kg = 0
        self.assert_prices(80, 120)
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.template.product_variant_ids[0].standard_price = 0
        self.assert_prices(80, 120)

    def test_normal_product_cost_is_unchanged_by_weight_options(self):
        self.template.shop_price_by_weight = False
        self.template.shop_cost_per_kg = 0
        before = {p.id: p.standard_price for p in self.template.product_variant_ids}
        self.template.action_load_shop_weight_options()
        for product_id, cost in before.items():
            self.assertEqual(self.env['product.product'].browse(product_id).standard_price, cost)

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
