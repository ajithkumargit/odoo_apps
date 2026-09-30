import mimetypes

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    shop_items_per_purchase_unit = fields.Float(
        string='Items in One Purchase Unit', default=1.0,
        help='Number of base items or kg in one purchased quantity. Example: a bag costing 2450 with 50 items gives a cost of 49 per item/kg. Leave blank or 0 to use 1. For a new order, confirmation uses a pack unit so receipts contain this many base units and the invoice total stays unchanged. Existing completed receipts are not rewritten.',
    )

    def _shop_prepare_pack_uom(self):
        # Represent a bag as a real purchase unit. Stock, received quantities,
        # returns and billing can then use the same standard UoM conversion.
        for line in self.filtered(lambda line: line.product_id and
                line.order_id.state in ('draft', 'sent') and
                line.shop_items_per_purchase_unit > 1 and
                line.product_uom_id == line.product_id.uom_id and
                not line.move_ids.filtered(lambda move: move.state == 'done')):
            base = line.product_id.uom_id
            count = line.shop_items_per_purchase_unit
            units = self.env['uom.uom']
            pack = units.search([
                ('relative_uom_id', '=', base.id), ('relative_factor', '=', count),
            ], limit=1)
            if not pack:
                pack = units.sudo().create({
                    'name': '%g %s pack' % (count, base.name),
                    'relative_uom_id': base.id, 'relative_factor': count,
                })
            # Preserve the agreed bag price when changing the purchase unit.
            line.write({'product_uom_id': pack.id, 'price_unit': line.price_unit})

    def write(self, vals):
        result = super().write(vals)
        if {'price_unit', 'discount', 'tax_ids', 'product_uom_id', 'shop_items_per_purchase_unit'}.intersection(vals):
            self.move_ids._shop_sync_received_purchase_cost()
        return result


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    def button_confirm(self):
        self.order_line._shop_prepare_pack_uom()
        return super().button_confirm()

    shop_bill_file = fields.Binary(
        string="Supplier Bill Image/PDF",
        attachment=True,
        copy=False,
    )
    shop_bill_file_name = fields.Char(string="Supplier Bill File Name", copy=False)
    shop_purchase_import_id = fields.Many2one(
        "shop.purchase.import",
        string="OCR Bill Import",
        copy=False,
        readonly=True,
    )
    shop_ocr_template_id = fields.Many2one(
        "shop.bill.ocr.template",
        string="Bill OCR Template",
        check_company=True,
        domain="[('company_id', '=', company_id), ('partner_id', '=', partner_id), ('active', '=', True)]",
    )
    shop_import_line_ids = fields.One2many(
        related="shop_purchase_import_id.line_ids",
        string="Extracted Bill Lines",
        readonly=False,
    )
    shop_import_state = fields.Selection(
        related="shop_purchase_import_id.state",
        string="OCR Review Status",
        readonly=True,
    )
    shop_import_currency_id = fields.Many2one(
        related="shop_purchase_import_id.currency_id",
        string="OCR Currency",
        readonly=True,
    )
    shop_import_untaxed_amount = fields.Monetary(
        related="shop_purchase_import_id.untaxed_amount",
        string="OCR Untaxed Cost",
        currency_field="shop_import_currency_id",
        readonly=True,
    )
    shop_import_tax_amount = fields.Monetary(
        related="shop_purchase_import_id.tax_amount",
        string="OCR Tax",
        currency_field="shop_import_currency_id",
        readonly=True,
    )
    shop_import_total_amount = fields.Monetary(
        related="shop_purchase_import_id.total_amount",
        string="OCR Total Cost",
        currency_field="shop_import_currency_id",
        readonly=True,
    )
    shop_import_unmatched_count = fields.Integer(
        related="shop_purchase_import_id.unmatched_line_count",
        readonly=True,
    )

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

    def action_shop_extract_bill(self):
        self.ensure_one()
        if self.state not in ("draft", "sent"):
            raise UserError(_("Bill OCR can only be used on a draft RFQ."))
        if not self.partner_id:
            raise UserError(_("Select the vendor before extracting the supplier bill."))
        if not self.shop_bill_file:
            raise UserError(_("Upload the supplier bill image or PDF first."))

        templates = self.env["shop.bill.ocr.template"].ensure_for_vendor(
            self.company_id, self.partner_id
        ).filtered("active")
        if self.shop_ocr_template_id not in templates:
            self.shop_ocr_template_id = templates if len(templates) == 1 else False
        if not self.shop_ocr_template_id:
            raise UserError(_(
                "This vendor has multiple bill layouts. Select the Bill OCR Template before extracting."
            ))

        purchase_import = self.shop_purchase_import_id
        if purchase_import and purchase_import.line_ids:
            raise UserError(
                _(
                    "This RFQ already has extracted lines. Review or remove those lines before extracting again."
                )
            )
        values = {
            "company_id": self.company_id.id,
            "currency_id": self.currency_id.id,
            "vendor_id": self.partner_id.id,
            "ocr_template_id": self.shop_ocr_template_id.id,
            "bill_date": fields.Date.context_today(self),
            "original_file": self.shop_bill_file,
            "original_file_name": self.shop_bill_file_name or "supplier_bill",
            "purchase_order_id": self.id,
        }
        if purchase_import:
            purchase_import.write(values)
        else:
            purchase_import = self.env["shop.purchase.import"].create(values)
            self.shop_purchase_import_id = purchase_import

        file_bytes, mimetype = purchase_import._prepare_bill_file()
        try:
            extracted_data, engine, fingerprint = purchase_import._extract_bill_locally(
                file_bytes, mimetype
            )
            purchase_import._apply_extracted_bill(
                extracted_data, engine, fingerprint
            )
        except UserError:
            purchase_import.extraction_status = "failed"
            raise

        if purchase_import.bill_number:
            self.partner_ref = purchase_import.bill_number
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Supplier bill extracted"),
                "message": _(
                    "Review product names, product matches, quantities, costs and taxes in the OCR Bill tab."
                ),
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }

    def action_shop_create_missing_products(self):
        self.ensure_one()
        if not self.shop_purchase_import_id:
            raise UserError(_("Extract a supplier bill first."))
        return self.shop_purchase_import_id.action_create_missing_products()

    def action_shop_match_taxes(self):
        self.ensure_one()
        if not self.shop_purchase_import_id:
            raise UserError(_("Extract a supplier bill first."))
        return self.shop_purchase_import_id.action_match_taxes()

    def action_shop_open_import(self):
        self.ensure_one()
        if not self.shop_purchase_import_id:
            raise UserError(_("Extract a supplier bill first."))
        return {
            "type": "ir.actions.act_window",
            "res_model": "shop.purchase.import",
            "view_mode": "form",
            "res_id": self.shop_purchase_import_id.id,
        }

    def action_shop_apply_ocr_lines(self):
        self.ensure_one()
        if self.state not in ("draft", "sent"):
            raise UserError(_("OCR lines can only be applied to a draft RFQ."))
        purchase_import = self.shop_purchase_import_id
        if not purchase_import:
            raise UserError(_("Extract a supplier bill first."))
        if self.order_line.filtered(lambda line: not line.display_type):
            raise UserError(
                _(
                    "This RFQ already contains product lines. Remove them before applying the OCR lines to prevent duplicates."
                )
            )

        purchase_import.write({
            "vendor_id": self.partner_id.id,
            "currency_id": self.currency_id.id,
        })
        purchase_import.action_match_products()
        purchase_import.action_mark_ready()
        purchase_import._create_lines_on_purchase_order(self)
        purchase_import.write({
            "purchase_order_id": self.id,
            "state": "po_created",
        })
        if purchase_import.bill_number:
            self.partner_ref = purchase_import.bill_number
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("OCR lines applied"),
                "message": _("The reviewed supplier-bill lines are now RFQ lines."),
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }

    def action_shop_receive_and_create_bill(self):
        self.ensure_one()
        if not self.order_line.filtered(lambda line: not line.display_type):
            raise UserError(_("Add or apply purchase-order lines first."))
        existing_bills = self.invoice_ids.filtered(lambda bill: bill.state != "cancel")
        if existing_bills:
            raise UserError(_("A vendor bill already exists for this purchase order."))

        if self.state in ("draft", "sent"):
            self.button_confirm()
        if self.state == "to approve":
            raise UserError(_("Approve the purchase order before receiving it."))
        if self.state != "purchase":
            raise UserError(_("The purchase order must be confirmed first."))

        pending_receipts = self.picking_ids.filtered(
            lambda picking: (
                picking.state not in ("done", "cancel")
                and picking.picking_type_code == "incoming"
            )
        )
        tracked_products = pending_receipts.move_ids.product_id.filtered(
            lambda product: product.tracking != "none"
        )
        if tracked_products:
            raise UserError(
                _(
                    "Receive lot/serial-tracked products manually first: %(products)s",
                    products=", ".join(tracked_products.mapped("display_name")),
                )
            )

        for receipt in pending_receipts:
            receipt.action_assign()
            open_moves = receipt.move_ids.filtered(
                lambda move: move.state not in ("done", "cancel")
            )
            for move in open_moves:
                move.quantity = move.product_uom_qty
            receipt.with_context(skip_backorder=True).button_validate()

        previous_bill_ids = self.invoice_ids.ids
        bill_action = self.action_create_invoice()
        new_bill = (self.invoice_ids - self.env["account.move"].browse(previous_bill_ids))[:1]
        purchase_import = self.shop_purchase_import_id
        if new_bill and purchase_import:
            new_bill.write({
                "invoice_date": purchase_import.bill_date,
                "ref": purchase_import.bill_number or self.partner_ref,
            })
            attachment_values = []
            if purchase_import.original_file:
                attachment_values.append({
                    "name": purchase_import.original_file_name or "supplier_bill_page_1",
                    "datas": purchase_import.original_file,
                })
            attachment_values.extend({
                "name": page.page_file_name or "supplier_bill_page_%s" % index,
                "datas": page.page_file,
            } for index, page in enumerate(
                purchase_import.page_ids.sorted(lambda item: (item.sequence, item.id)),
                start=2,
            ))
            for values in attachment_values:
                values.update({
                    "mimetype": mimetypes.guess_type(values["name"])[0]
                    or "application/octet-stream",
                    "res_model": "account.move",
                    "res_id": new_bill.id,
                })
            if attachment_values:
                attachments = self.env["ir.attachment"].create(attachment_values)
                new_bill.message_post(attachment_ids=attachments.ids)
        return bill_action

    def action_shop_revert_import(self):
        """Reverse an OCR import using proper stock returns and document cancellation."""
        self.ensure_one()
        purchase_import = self.shop_purchase_import_id or self.env["shop.purchase.import"].search(
            [("purchase_order_id", "=", self.id)], limit=1
        )
        if not purchase_import:
            raise UserError(_("This purchase order is not linked to a bill import."))

        posted_bills = self.invoice_ids.filtered(lambda bill: bill.state == "posted")
        if posted_bills:
            raise UserError(_(
                "Revert is blocked because vendor bill(s) %(bills)s are posted. "
                "Reset or reverse them in Accounting first.",
                bills=", ".join(posted_bills.mapped("name")),
            ))

        draft_bills = self.invoice_ids.filtered(lambda bill: bill.state == "draft")
        if draft_bills:
            draft_bills.button_cancel()

        completed_receipts = self.picking_ids.filtered(
            lambda picking: (
                picking.state == "done"
                and picking.picking_type_code == "incoming"
                and not picking.return_id
            )
        )
        for receipt in completed_receipts:
            return_wizard = self.env["stock.return.picking"].with_context(
                active_id=receipt.id,
                active_ids=receipt.ids,
                active_model="stock.picking",
            ).create({"picking_id": receipt.id})
            return_action = return_wizard.action_create_returns_all()
            return_picking = self.env["stock.picking"].browse(return_action["res_id"])
            for move in return_picking.move_ids.filtered(
                lambda stock_move: stock_move.state not in ("done", "cancel")
            ):
                move.quantity = move.product_uom_qty
            return_picking.with_context(skip_backorder=True).button_validate()

        if self.state != "cancel":
            self.button_cancel()
        self.shop_purchase_import_id = False
        purchase_import.write({
            "purchase_order_id": False,
            "state": "review",
        })
        return {
            "type": "ir.actions.act_window",
            "res_model": "shop.purchase.import",
            "view_mode": "form",
            "res_id": purchase_import.id,
        }
