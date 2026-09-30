from odoo import Command
from odoo.tests.common import TransactionCase


class TestBulkPurchaseFlow(TransactionCase):
    def setUp(self):
        super().setUp()
        attribute = self.env['product.attribute'].create({'name': 'Bulk test weight'})
        weights = self.env['product.attribute.value'].create([
            {'name': name, 'attribute_id': attribute.id} for name in ['250g', '500g', '1kg']])
        self.template = self.env['product.template'].create({
            'name': 'Bulk flow test', 'list_price': 56,
            'shop_price_by_weight': True, 'shop_cost_per_kg': 51,
            'attribute_line_ids': [Command.create({'attribute_id': attribute.id,
                'value_ids': [Command.set(weights.ids)]})],
        })

    def test_bag_receipt_and_invoice_quantities(self):
        kg = self.env.ref('uom.product_uom_kgm')
        self.template.write({'uom_id': kg.id, 'is_storable': True})
        product = self.template.product_variant_ids.filtered(lambda p: p.shop_variant_weight_grams == 1000)
        vendor = self.env['res.partner'].create({'name': 'Bulk supplier'})
        order = self.env['purchase.order'].create({
            'partner_id': vendor.id,
            'order_line': [Command.create({'product_id': product.id,
                'product_qty': 1, 'product_uom_id': kg.id, 'price_unit': 2450,
                'shop_items_per_purchase_unit': 50, 'tax_ids': [Command.clear()]})],
        })
        order.button_confirm()
        self.assertEqual(order.amount_total, 2450)
        for receipt in order.picking_ids:
            for move in receipt.move_ids:
                move.quantity = move.product_uom_qty
            receipt.with_context(skip_backorder=True).button_validate()
        self.assertEqual(product.qty_available, 50)
        self.assertEqual(order.order_line.qty_received, 1)
        self.assertAlmostEqual(self.template.shop_cost_per_kg, 49)
        order.action_create_invoice()
        bill_line = order.invoice_ids.invoice_line_ids.filtered('product_id')
        self.assertEqual(bill_line.quantity, 1)
        self.assertEqual(bill_line.price_subtotal, 2450)

    def test_weight_sales_use_bulk_stock_and_refunds_restore_it(self):
        self._check_bulk_stock_sales(2, -1)

    def test_kg_sales_use_bulk_stock_without_converting_weight_twice(self):
        self.template.uom_id = self.env.ref('uom.product_uom_kgm')
        self._check_bulk_stock_sales(0.5, -0.25)

    def _check_bulk_stock_sales(self, sale_quantity, refund_quantity):
        self.template.is_storable = True
        variants = self.template.product_variant_ids
        source = variants.filtered(lambda p: p.shop_variant_weight_grams == 1000)
        quarter = variants.filtered(lambda p: p.shop_variant_weight_grams == 250)
        self.template.shop_bulk_stock_product_id = source
        warehouse = self.env['stock.warehouse'].search([('company_id', '=', self.env.company.id)], limit=1)
        location = warehouse.lot_stock_id
        customer = self.env.ref('stock.stock_location_customers')
        self.env['stock.quant']._update_available_quantity(source, location, 50)
        # Exercise the actual POS move preparation and stock completion hooks.
        for quantity, expected in [(sale_quantity, 49.5), (refund_quantity, 49.75)]:
            line = self.env['pos.order.line'].new({'product_id': quarter.id, 'qty': quantity})
            picking = self.env['stock.picking'].create({
                'picking_type_id': warehouse.out_type_id.id,
                'location_id': location.id if quantity > 0 else customer.id,
                'location_dest_id': customer.id if quantity > 0 else location.id,
            })
            picking._create_move_from_pos_order_lines(line)
            picking._action_done()
            self.assertEqual(picking.move_ids.product_id, source)
            self.assertAlmostEqual(source.qty_available, expected)
            self.assertEqual(quarter.qty_available, 0)
        self.template.shop_bulk_stock_product_id = False
        self.assertEqual(quarter._shop_bulk_stock_quantity(2), (quarter, 2))

    def test_bill_for_archived_bulk_product_updates_unit_weight_variants(self):
        template = self.env['product.template'].create({
            'name': 'Original loose bulk product', 'standard_price': 90, 'list_price': 110})
        original = template.product_variant_id
        vendor = self.env['res.partner'].create({'name': 'Bulk bill supplier'})
        order = self.env['purchase.order'].create({'partner_id': vendor.id})
        bill = self.env['shop.purchase.import'].create({
            'vendor_id': vendor.id, 'state': 'po_created', 'purchase_order_id': order.id})
        line = self.env['shop.purchase.import.line'].create({
            'import_id': bill.id, 'product_id': original.id, 'quantity': 1,
            'raw_description': 'Bulk 30 kg', 'purchase_rate': 3050.1,
            'units_per_purchase_qty': 30})
        attribute_line = self.template.attribute_line_ids
        template.write({'attribute_line_ids': [Command.create({
            'attribute_id': attribute_line.attribute_id.id,
            'value_ids': [Command.set(attribute_line.value_ids.ids)]})]})
        template.write({'shop_price_by_weight': True, 'shop_cost_per_kg': 90})
        self.assertFalse(original.active)
        template.action_update_cost_from_bill()
        self.assertAlmostEqual(template.shop_cost_per_kg, 101.67)
        for variant in template.product_variant_ids:
            self.assertAlmostEqual(variant.standard_price, 101.67 * variant.shop_variant_weight_grams / 1000)
        line.purchase_rate = 3000
        self.assertAlmostEqual(template.shop_cost_per_kg, 100)
        bill._create_lines_on_purchase_order(order)
        kilogram = template.product_variant_ids.filtered(lambda product: product.shop_variant_weight_grams == 1000)
        self.assertEqual(order.order_line.product_id, kilogram)
        self.assertEqual(order.order_line.product_qty, 30)
        self.assertEqual(order.order_line.price_unit, 100)

    def test_linked_refund_keeps_original_stock_after_source_changes(self):
        variants = self.template.product_variant_ids
        source = variants.filtered(lambda p: p.shop_variant_weight_grams == 1000)
        quarter = variants.filtered(lambda p: p.shop_variant_weight_grams == 250)
        self.template.shop_bulk_stock_product_id = source
        config = self.env['pos.config'].create({'name': 'Bulk stock test POS'})
        session = self.env['pos.session'].create({'config_id': config.id, 'user_id': self.env.uid})
        order = self.env['pos.order'].create({
            'session_id': session.id, 'company_id': self.env.company.id,
            'amount_total': 28, 'amount_tax': 0, 'amount_paid': 0, 'amount_return': 0,
            'lines': [Command.create({'product_id': quarter.id, 'qty': 2,
                'price_unit': 14, 'price_subtotal': 28, 'price_subtotal_incl': 28})]})
        warehouse = config.picking_type_id.warehouse_id
        picking = self.env['stock.picking'].create({
            'picking_type_id': warehouse.out_type_id.id,
            'location_id': warehouse.lot_stock_id.id,
            'location_dest_id': self.env.ref('stock.stock_location_customers').id})
        picking._create_move_from_pos_order_lines(order.lines)
        self.assertEqual(order.lines.shop_stock_product_id, source)
        self.assertEqual(order.lines.shop_stock_quantity_factor, 0.25)
        self.template.shop_bulk_stock_product_id = False
        refund = self.env['pos.order.line'].new({
            'product_id': quarter.id, 'qty': -1, 'refunded_orderline_id': order.lines.id})
        self.assertEqual(refund._shop_stock_target(), (source, 0.25))
        # A pre-upgrade sale has no snapshot and returns to its original variant.
        order.lines.write({'shop_stock_product_id': False, 'shop_stock_quantity_factor': 0})
        self.template.shop_bulk_stock_product_id = source
        self.assertEqual(refund._shop_stock_target(), (quarter, 1))

    def test_partial_bag_receipt_and_return(self):
        kg = self.env.ref('uom.product_uom_kgm')
        self.template.write({'uom_id': kg.id, 'is_storable': True})
        product = self.template.product_variant_ids.filtered(lambda p: p.shop_variant_weight_grams == 1000)
        vendor = self.env['res.partner'].create({'name': 'Partial bag supplier'})
        order = self.env['purchase.order'].create({
            'partner_id': vendor.id,
            'order_line': [Command.create({'product_id': product.id,
                'product_qty': 1, 'product_uom_id': kg.id, 'price_unit': 2450,
                'shop_items_per_purchase_unit': 50, 'tax_ids': [Command.clear()]})]})
        order.button_confirm()
        receipt = order.picking_ids
        for move in receipt.move_ids:
            move.quantity = kg._compute_quantity(20, move.product_uom)
        receipt.with_context(skip_backorder=True).button_validate()
        self.assertEqual(product.qty_available, 20)
        self.assertAlmostEqual(order.order_line.qty_received, 0.4)
        self.assertAlmostEqual(self.template.shop_cost_per_kg, 49)
        wizard = self.env['stock.return.picking'].with_context(
            active_id=receipt.id, active_model='stock.picking').create({'picking_id': receipt.id})
        action = wizard.action_create_returns_all()
        returned = self.env['stock.picking'].browse(action['res_id'])
        for move in returned.move_ids:
            move.quantity = move.product_uom_qty
        returned.with_context(skip_backorder=True).button_validate()
        self.assertEqual(product.qty_available, 0)
        self.assertAlmostEqual(order.order_line.qty_received, 0)
        self.assertAlmostEqual(self.template.shop_cost_per_kg, 49)
