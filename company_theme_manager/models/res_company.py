from odoo import fields, models

class ResCompany(models.Model):
    _inherit = "res.company"

    theme_id = fields.Many2one(
        "company.theme",
        string="Company Theme",
        domain="[('company_id', '=', id)]",
        help="Theme used for this company.",
    )