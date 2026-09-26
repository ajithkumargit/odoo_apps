from odoo import api, fields, models


class ShopBillProductNameMap(models.Model):
    _name = "shop.bill.product.name.map"
    _description = "Bill Product Name Mapping"
    _order = "bill_product_name"

    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    bill_product_name = fields.Char(
        string="Name on Bill",
        required=True,
        index=True,
        help="Supplier's short or abbreviated product name printed on the bill.",
    )
    normalized_bill_product_name = fields.Char(
        compute="_compute_normalized_bill_product_name",
        store=True,
        precompute=True,
        index=True,
    )
    product_id = fields.Many2one(
        "product.product",
        string="Odoo Product",
        required=True,
        domain="[('purchase_ok', '=', True)]",
        index=True,
    )

    _company_bill_name_unique = models.Constraint(
        "UNIQUE(company_id, normalized_bill_product_name)",
        "This bill product name is already connected to a product.",
    )

    @api.model
    def normalize_name(self, value):
        return " ".join((value or "").strip().upper().split())

    @api.depends("bill_product_name")
    def _compute_normalized_bill_product_name(self):
        for mapping in self:
            mapping.normalized_bill_product_name = self.normalize_name(
                mapping.bill_product_name
            )

    @api.model
    def find_product(self, company, bill_product_name):
        normalized_name = self.normalize_name(bill_product_name)
        if not company or not normalized_name:
            return self.env["product.product"]
        mapping = self.search(
            [
                ("company_id", "=", company.id),
                ("normalized_bill_product_name", "=", normalized_name),
            ],
            limit=1,
        )
        return mapping.product_id

    @api.model
    def connect(self, company, bill_product_name, product):
        normalized_name = self.normalize_name(bill_product_name)
        if not company or not normalized_name or not product:
            return self.browse()
        mapping = self.with_context(active_test=False).search(
            [
                ("company_id", "=", company.id),
                ("normalized_bill_product_name", "=", normalized_name),
            ],
            limit=1,
        )
        values = {
            "company_id": company.id,
            "bill_product_name": bill_product_name.strip(),
            "product_id": product.id,
            "active": True,
        }
        if mapping:
            mapping.write(values)
            return mapping
        return self.create(values)
