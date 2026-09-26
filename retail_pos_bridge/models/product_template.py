from odoo import api, fields, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    retail_wholesale_min_qty = fields.Float(
        string="Wholesale Minimum Qty",
        default=3.0,
        help="Quantity from which the custom POS uses calculated wholesale pricing.",
    )
    retail_purchase_tax_percent = fields.Float(
        string="Purchase Tax %",
        default=0.0,
        help="Purchase tax percent used by the custom POS selling price formula.",
    )
    retail_profit_percent = fields.Float(
        string="Profit %",
        default=10.0,
        help="Profit percent used by the custom POS selling price formula.",
    )
    retail_calculated_selling_price = fields.Monetary(
        string="Calculated Selling Price",
        compute="_compute_retail_calculated_selling_price",
        currency_field="currency_id",
        store=False,
    )

    @api.depends("standard_price", "retail_purchase_tax_percent", "retail_profit_percent")
    def _compute_retail_calculated_selling_price(self):
        for product in self:
            taxed_cost = product.standard_price * (1 + product.retail_purchase_tax_percent / 100)
            product.retail_calculated_selling_price = taxed_cost * (
                1 + product.retail_profit_percent / 100
            )
