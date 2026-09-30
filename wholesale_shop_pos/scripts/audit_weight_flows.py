"""Read-only report for an Odoo shell. Call audit_weight_flows(env)."""


def audit_weight_flows(env):
    products = []
    for template in env['product.template'].search([('shop_price_by_weight', '=', True)]):
        variants = env['product.product'].with_context(active_test=False).search([
            ('product_tmpl_id', '=', template.id)])
        products.append({
            'id': template.id, 'name': template.name, 'unit': template.uom_id.name,
            'cost_per_kg': template.shop_cost_per_kg,
            'sale_per_kg': template.shop_sale_price_per_kg,
            'profit_percent': template.shop_profit_percent,
            'bulk_stock_product_id': template.shop_bulk_stock_product_id.id or None,
            'variants': [{
                'id': product.id, 'active': product.active,
                'grams': product.shop_variant_weight_grams,
                'cost': product.standard_price, 'sales_price': product.lst_price,
                'quantity_on_hand': product.qty_available,
            } for product in variants],
        })
    purchases = []
    for line in env['purchase.order.line'].search([
            ('product_id.product_tmpl_id.shop_price_by_weight', '=', True),
            ('order_id.state', '=', 'purchase')], order='order_id desc, id'):
        purchases.append({
            'order': line.order_id.name, 'line_id': line.id,
            'product_id': line.product_id.id, 'quantity': line.product_qty,
            'received': line.qty_received, 'unit': line.product_uom_id.name,
            'price': line.price_unit, 'items_per_purchase_unit': line.shop_items_per_purchase_unit,
            'total': line.price_total,
            'count_without_stock_conversion': bool(line.shop_items_per_purchase_unit > 1
                and line.product_uom_id == line.product_id.uom_id and line.qty_received > 0),
        })
    return {'database': env.cr.dbname, 'weighted_products': products, 'received_purchases': purchases}
