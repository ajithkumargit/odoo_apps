from odoo import api, fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    shop_is_vendor = fields.Boolean(
        string="Vendor",
        default=False,
        index=True,
        help="Use this contact as a vendor in Wholesale Shop purchase workflows.",
    )
    shop_is_customer = fields.Boolean(
        string="Customer",
        default=False,
        index=True,
        help="Use this contact as a customer in Wholesale Shop sales workflows.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        vendor_default = bool(self.env.context.get("default_shop_is_vendor"))
        customer_default = bool(self.env.context.get("default_shop_is_customer"))
        for values in vals_list:
            if vendor_default:
                values.setdefault("shop_is_vendor", True)
            if customer_default:
                values.setdefault("shop_is_customer", True)
        return super().create(vals_list)

    def _mark_as_shop_vendor(self):
        vendors = self.filtered(lambda partner: not partner.shop_is_vendor)
        if vendors:
            vendors.write({"shop_is_vendor": True})
        return self

    def _mark_as_shop_customer(self):
        customers = self.filtered(lambda partner: not partner.shop_is_customer)
        if customers:
            customers.write({"shop_is_customer": True})
        return self
