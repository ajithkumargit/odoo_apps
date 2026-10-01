from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    templates = env["shop.bill.ocr.template"].with_context(active_test=False).search([
        ("partner_id.name", "ilike", "Dinesh Enterprises"),
        ("data_rows_per_item", "=", 1),
    ])
    # These invoices print the item and price on the first row, then HSN,
    # secondary quantity and component taxes on the next printed row.
    templates.write({"data_rows_per_item": 2})
