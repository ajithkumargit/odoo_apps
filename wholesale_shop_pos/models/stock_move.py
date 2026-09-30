from odoo import models


class StockMove(models.Model):
    _inherit = 'stock.move'

    def _action_done(self, cancel_backorder=False):
        completed = super()._action_done(cancel_backorder=cancel_backorder)
        completed._shop_sync_received_purchase_cost()
        return completed

    def _shop_sync_received_purchase_cost(self):
        # Apply the latest received purchase cost after stock's valuation work.
        # Exclude returns, internal transfers, unreceived and free quantities.
        for move in self.sorted('id').filtered(lambda move:
                move.state == 'done' and move.quantity > 0 and move.purchase_line_id
                and move.purchase_line_id.order_id.state == 'purchase'
                and move.location_id.usage == 'supplier'
                and move.location_dest_id.usage == 'internal'
                and not move.origin_returned_move_id):
            line = move.purchase_line_id
            if line.product_qty <= 0 or line.price_total <= 0:
                continue
            product = move.product_id.with_company(move.company_id)
            cost = line.price_total / line.product_qty
            items = line.shop_items_per_purchase_unit if line.shop_items_per_purchase_unit > 0 else 1.0
            kg = self.env.ref('uom.product_uom_kgm')
            target_uom = kg if product.shop_price_by_weight and line.product_uom_id == kg else product.uom_id
            # An explicit item count already expresses the cost in base units;
            # do not also apply the purchase-UoM conversion a second time.
            cost = cost / items if items != 1 else line.product_uom_id._compute_price(cost, target_uom)
            cost = line.currency_id._convert(cost, move.company_id.currency_id, move.company_id, move.date)
            if cost <= 0:
                continue
            if product.shop_price_by_weight and target_uom == kg:
                product.product_tmpl_id.shop_cost_per_kg = cost
            else:
                product.standard_price = cost
