from odoo.tests.common import TransactionCase


class TestManualSalePrice(TransactionCase):
    def test_manual_template_price_survives_cost_and_unchanged_percent(self):
        p = self.env['product.template'].create({'name': 'Manual price', 'standard_price': 100, 'shop_profit_percent': 20})
        self.assertEqual(p.list_price, 120)
        p.list_price = 123.45
        p.standard_price = 110
        p.shop_profit_percent = 20
        self.assertEqual(p.list_price, 123.45)
        p.shop_profit_percent = 25
        self.assertEqual(p.list_price, 137.5)
        p.shop_profit_percent = 0
        self.assertEqual(p.list_price, 110)

    def test_variant_manual_price_survives_cost_import(self):
        p = self.env['product.product'].create({'name': 'Manual variant', 'standard_price': 100, 'shop_variant_profit_percent': 20})
        self.assertEqual(p.lst_price, 120)
        p.lst_price = 123.45
        p.standard_price = 115
        p.shop_variant_profit_percent = 20
        self.assertEqual(p.lst_price, 123.45)
        self.assertEqual(p.product_tmpl_id.list_price, 123.45)
        self.assertEqual(p._price_compute('list_price')[p.id], 123.45)
        p.shop_variant_profit_percent = 30
        self.assertEqual(p.lst_price, 149.5)
        p.shop_variant_sale_price = 151.25
        self.assertEqual(p.lst_price, 151.25)
        p.product_tmpl_id.list_price = 160
        self.assertEqual(p.lst_price, 160)

    def test_direct_price_wins_when_percent_also_supplied(self):
        p = self.env['product.product'].create({'name': 'Explicit price', 'standard_price': 100,
            'shop_variant_profit_percent': 20, 'list_price': 159.99})
        self.assertEqual(p.lst_price, 159.99)
        history = self.env['shop.product.price.history']
        sales = history.search([('product_id', '=', p.id), ('price_type', '=', 'sale')])
        self.assertEqual(len(sales), 1)
        self.assertEqual(sales.unit_price, 159.99)
        p.write({'lst_price': 177.77, 'shop_variant_profit_percent': 30})
        self.assertEqual(p.lst_price, 177.77)
        sales = history.search([('product_id', '=', p.id), ('price_type', '=', 'sale')], order='id desc')
        self.assertEqual(len(sales), 2)
        self.assertEqual(sales[0].previous_price, 159.99)
        self.assertEqual(sales[0].unit_price, 177.77)
        p.write({'standard_price': 200})
        self.assertEqual(p.lst_price, 177.77)

    def test_regular_purchase_receipt_uses_positive_variant_profit(self):
        product = self.env['product.product'].create({
            'name': 'Regular purchased pack', 'is_storable': True,
            'standard_price': 100, 'shop_variant_profit_percent': 20})
        vendor = self.env['res.partner'].create({'name': 'Regular supplier'})
        order = self.env['purchase.order'].create({
            'partner_id': vendor.id,
            'order_line': [(0, 0, {'product_id': product.id,
                'product_qty': 1, 'product_uom_id': product.uom_id.id,
                'price_unit': 120, 'tax_ids': [(5, 0, 0)]})],
        })
        order.button_confirm()
        for picking in order.picking_ids:
            for move in picking.move_ids:
                move.quantity = move.product_uom_qty
            picking.with_context(skip_backorder=True).button_validate()
        self.assertEqual(product.standard_price, 120)
        self.assertEqual(product.lst_price, 144)
        order.order_line.price_unit = 130
        self.assertEqual(product.standard_price, 130)
        self.assertEqual(product.lst_price, 156)

    def test_six_decimal_percentage(self):
        p = self.env['product.template'].create({'name': 'Precise percent', 'standard_price': 81.89})
        p.shop_profit_percent = 22.114971
        self.assertAlmostEqual(p.shop_profit_percent, 22.114971, places=6)
        self.assertEqual(p.list_price, 100)

    def test_manual_variant_edit_does_not_change_sibling(self):
        a = self.env['product.attribute'].create({'name': 'Price size'})
        v = self.env['product.attribute.value'].create([{'name': 'Small', 'attribute_id': a.id}, {'name': 'Large', 'attribute_id': a.id}])
        t = self.env['product.template'].create({'name': 'Manual variants', 'list_price': 50,
            'attribute_line_ids': [(0, 0, {'attribute_id': a.id, 'value_ids': [(6, 0, v.ids)]})]})
        first, second = t.product_variant_ids.sorted('id')
        first.lst_price = 67.89
        self.assertEqual(first.lst_price, 67.89)
        self.assertEqual(second.lst_price, 50)
