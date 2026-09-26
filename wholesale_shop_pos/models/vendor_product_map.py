from odoo import api, fields, models


class ShopVendorProductMap(models.Model):
    _name = "shop.vendor.product.map"
    _description = "Vendor Product Mapping"
    _order = "partner_id, vendor_product_name"

    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    partner_id = fields.Many2one(
        "res.partner",
        string="Vendor",
        required=True,
        index=True,
        check_company=True,
        domain="[('shop_is_vendor', '=', True)]",
    )
    vendor_product_name = fields.Char(string="Vendor Product Name", required=True, index=True)
    vendor_product_code = fields.Char(string="Vendor Product Code", index=True)
    barcode = fields.Char(string="Vendor Barcode", index=True)
    product_id = fields.Many2one(
        "product.product",
        string="Odoo Product",
        required=True,
        domain="[('purchase_ok', '=', True)]",
        index=True,
    )
    note = fields.Text()

    _sql_constraints = [
        (
            "vendor_name_company_uniq",
            "unique(company_id, partner_id, vendor_product_name)",
            "This vendor product name is already mapped for this company.",
        )
    ]

    @api.model
    def normalize_name(self, value):
        return " ".join((value or "").strip().upper().split())

    @api.model_create_multi
    def create(self, vals_list):
        partner_ids = {
            values.get("partner_id")
            for values in vals_list
            if values.get("partner_id")
        }
        if partner_ids:
            self.env["res.partner"].browse(partner_ids)._mark_as_shop_vendor()
        return super().create(vals_list)

    def write(self, vals):
        if vals.get("partner_id"):
            self.env["res.partner"].browse(vals["partner_id"])._mark_as_shop_vendor()
        return super().write(vals)
