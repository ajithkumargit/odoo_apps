from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    products = env['product.product'].with_context(active_test=False).search([
        ('shop_variant_profit_percent', '>', 0), ('shop_sale_price_fixed', '=', False),
    ])
    for product in products:
        product.with_company(product.company_id or env.company)._freeze_legacy_shop_price()
