from odoo import fields, models


class ShopProductPriceHistory(models.Model):
    _name = "shop.product.price.history"
    _description = "Product Price History"
    _order = "changed_at desc, id desc"

    price_type = fields.Selection(
        [("purchase", "Purchase"), ("cost", "Cost"), ("sale", "Sales Price")],
        default="purchase", required=True, index=True,
    )
    previous_price = fields.Float(string="Previous Price", digits=(16, 6))
    changed_at = fields.Datetime(default=fields.Datetime.now, required=True, index=True)
    changed_by_id = fields.Many2one("res.users", string="Changed By", default=lambda self: self.env.user)

    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    product_id = fields.Many2one("product.product", required=True, index=True)
    partner_id = fields.Many2one(
        "res.partner",
        string="Vendor",
        index=True,
        domain="[('shop_is_vendor', '=', True)]",
    )
    purchase_import_id = fields.Many2one("shop.purchase.import", ondelete="set null", index=True)
    purchase_order_id = fields.Many2one("purchase.order", ondelete="set null", index=True)
    purchase_date = fields.Date(default=fields.Date.context_today, required=True, index=True)
    currency_id = fields.Many2one(
        "res.currency",
        required=True,
        default=lambda self: self.env.company.currency_id,
    )
    qty = fields.Float(default=1.0)
    unit_price = fields.Float(string="New / Purchase Price", required=True, digits=(16, 6))
    source_description = fields.Char()
