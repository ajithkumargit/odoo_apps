from collections import defaultdict

from odoo import fields, models


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    shop_bulk_stock_product_id = fields.Many2one(
        'product.product', string='Bulk Stock Product', check_company=True, copy=False,
        domain="[('product_tmpl_id', '=', id)]",
        help='Optional: select the 1 kg variant that holds loose stock. POS sales and refunds of weight variants use this stock. Leave empty for separately stocked packs. Existing stock is not moved.',
    )


class ProductProduct(models.Model):
    _inherit = 'product.product'

    def _shop_bulk_stock_quantity(self, quantity):
        self.ensure_one()
        source = self.shop_bulk_stock_product_id
        if (not self.shop_price_by_weight or not source or source == self
                or source.product_tmpl_id != self.product_tmpl_id
                or source.tracking != 'none' or self.tracking != 'none'):
            return self, quantity
        weight_attribute = self.shop_weight_attribute_id
        other_values = self.product_template_attribute_value_ids.filtered(
            lambda value: value.attribute_id != weight_attribute)
        source_values = source.product_template_attribute_value_ids.filtered(
            lambda value: value.attribute_id != weight_attribute)
        if other_values != source_values:
            return self, quantity
        if self.uom_id == self.env.ref('uom.product_uom_kgm'):
            return source, quantity
        grams = self.shop_variant_weight_grams
        source_grams = source.shop_variant_weight_grams
        if grams <= 0 or source_grams <= 0:
            return self, quantity
        return source, quantity * grams / source_grams


class PosOrderLine(models.Model):
    _inherit = 'pos.order.line'

    # Capture the stock basis at sale time: later configuration changes must
    # not redirect a refund into a different product's inventory.
    shop_stock_product_id = fields.Many2one('product.product', copy=False)
    shop_stock_quantity_factor = fields.Float(copy=False, digits=(16, 8))

    def _shop_stock_target(self):
        self.ensure_one()
        original = self.refunded_orderline_id
        if self.qty < 0 and original:
            return (original.shop_stock_product_id or original.product_id,
                    original.shop_stock_quantity_factor or 1.0)
        return self.product_id._shop_bulk_stock_quantity(1.0)


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def _create_move_from_pos_order_lines(self, lines):
        # The same variant can be refunded from sales with different stock
        # sources. Keep those quantities apart before the core grouping runs.
        groups = defaultdict(lambda: self.env['pos.order.line'])
        for line in lines:
            source, factor = line._shop_stock_target()
            groups[(source.id, factor)] |= line
        for group in groups.values():
            super()._create_move_from_pos_order_lines(group)

    def _prepare_stock_move_vals(self, first_line, order_lines):
        values = super()._prepare_stock_move_vals(first_line, order_lines)
        source, factor = first_line._shop_stock_target()
        values.update(product_id=source.id, product_uom=source.uom_id.id,
                      product_uom_qty=values['product_uom_qty'] * factor)
        for line in order_lines.filtered(lambda line: line.id and line.qty > 0):
            line.write({'shop_stock_product_id': source.id,
                        'shop_stock_quantity_factor': factor})
        return values
