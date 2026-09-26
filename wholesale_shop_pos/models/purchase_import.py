import base64
from io import BytesIO
import json
import logging
import mimetypes
import re
from difflib import SequenceMatcher

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools.float_utils import float_compare

from .local_bill_ocr import LocalOCRError, extract_bill


_logger = logging.getLogger(__name__)

SUPPORTED_BILL_MIMETYPES = {
    "application/pdf",
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
}
MAX_BILL_FILE_SIZE = 20 * 1024 * 1024
MAX_BILL_TOTAL_SIZE = 100 * 1024 * 1024
MAX_BILL_DOCUMENTS = 20


class ShopPurchaseImport(models.Model):
    _name = "shop.purchase.import"
    _description = "Purchase Bill Import"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "bill_date desc, id desc"

    name = fields.Char(default=lambda self: _("New"), copy=False, readonly=True, tracking=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    currency_id = fields.Many2one(
        "res.currency",
        required=True,
        default=lambda self: self.env.company.currency_id,
    )
    vendor_id = fields.Many2one(
        "res.partner",
        string="Vendor",
        tracking=True,
        index=True,
        check_company=True,
        domain="[('shop_is_vendor', '=', True)]",
    )
    ocr_template_id = fields.Many2one(
        "shop.bill.ocr.template",
        string="Bill OCR Template",
        check_company=True,
        domain="[('company_id', '=', company_id), ('partner_id', '=', vendor_id), ('active', '=', True)]",
        help="Select the vendor's bill layout. It is selected automatically when only one template exists.",
    )
    bill_number = fields.Char(tracking=True, index=True)
    bill_date = fields.Date(default=fields.Date.context_today, required=True, tracking=True)
    original_file = fields.Binary(string="Original Bill", attachment=True)
    original_file_name = fields.Char(string="Original File Name")
    page_ids = fields.One2many(
        "shop.purchase.import.page",
        "import_id",
        string="Continuation Pages",
        copy=True,
    )
    original_is_image = fields.Boolean(compute="_compute_original_is_image")
    manual_crop_enabled = fields.Boolean(string="Use Manual Crop", copy=False)
    crop_left = fields.Float(default=0.0, copy=False)
    crop_top = fields.Float(default=0.0, copy=False)
    crop_right = fields.Float(default=100.0, copy=False)
    crop_bottom = fields.Float(default=100.0, copy=False)
    raw_extracted_text = fields.Text(
        help="Optional raw OCR/extraction payload retained for audit and future reprocessing."
    )
    extracted_vendor_name = fields.Char(readonly=True, copy=False)
    extraction_status = fields.Selection(
        [
            ("not_extracted", "Not Extracted"),
            ("done", "Extracted"),
            ("failed", "Failed"),
        ],
        default="not_extracted",
        required=True,
        readonly=True,
        copy=False,
    )
    extraction_model = fields.Char(string="Local OCR Engine", readonly=True, copy=False)
    extraction_response_id = fields.Char(string="File Fingerprint", readonly=True, copy=False)
    extracted_at = fields.Datetime(readonly=True, copy=False)
    state = fields.Selection(
        [
            ("draft", "Uploaded"),
            ("review", "Review"),
            ("ready", "Ready"),
            ("po_created", "Purchase Order Created"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
        tracking=True,
        index=True,
    )
    line_ids = fields.One2many("shop.purchase.import.line", "import_id", string="Bill Lines", copy=True)
    purchase_order_id = fields.Many2one("purchase.order", copy=False, readonly=True, tracking=True)
    note = fields.Text()

    untaxed_amount = fields.Monetary(compute="_compute_amounts", store=True, currency_field="currency_id")
    tax_amount = fields.Monetary(compute="_compute_amounts", store=True, currency_field="currency_id")
    total_amount = fields.Monetary(compute="_compute_amounts", store=True, currency_field="currency_id")
    unmatched_line_count = fields.Integer(compute="_compute_match_counts")
    matched_line_count = fields.Integer(compute="_compute_match_counts")

    @api.depends("original_file_name", "original_file")
    def _compute_original_is_image(self):
        for rec in self:
            rec.original_is_image = bool(rec.original_file) and not (
                (rec.original_file_name or "").lower().endswith(".pdf")
            )

    @api.onchange("original_file")
    def _onchange_original_file_reset_crop(self):
        self.manual_crop_enabled = False
        self.crop_left, self.crop_top = 0.0, 0.0
        self.crop_right, self.crop_bottom = 100.0, 100.0

    def action_reset_manual_crop(self):
        self.ensure_one()
        self.write({
            "manual_crop_enabled": False,
            "crop_left": 0.0, "crop_top": 0.0,
            "crop_right": 100.0, "crop_bottom": 100.0,
        })
        return True

    def _apply_manual_crop(self, file_bytes, mimetype):
        self.ensure_one()
        return self._crop_bill_image(
            file_bytes,
            mimetype,
            self.manual_crop_enabled,
            (self.crop_left, self.crop_top, self.crop_right, self.crop_bottom),
        )

    @api.depends("line_ids.taxable_amount", "line_ids.tax_amount", "line_ids.total_amount")
    def _compute_amounts(self):
        for rec in self:
            rec.untaxed_amount = sum(rec.line_ids.mapped("taxable_amount"))
            rec.tax_amount = sum(rec.line_ids.mapped("tax_amount"))
            rec.total_amount = sum(rec.line_ids.mapped("total_amount"))

    @api.depends("line_ids.product_id")
    def _compute_match_counts(self):
        for rec in self:
            rec.matched_line_count = len(rec.line_ids.filtered("product_id"))
            rec.unmatched_line_count = len(rec.line_ids.filtered(lambda l: not l.product_id))

    @api.model_create_multi
    def create(self, vals_list):
        vendor_ids = {
            values.get("vendor_id") for values in vals_list if values.get("vendor_id")
        }
        if vendor_ids:
            self.env["res.partner"].browse(vendor_ids)._mark_as_shop_vendor()
        records = super().create(vals_list)
        for rec in records:
            if rec.name == _("New"):
                rec.name = "PBI/%s" % rec.id
            if rec.vendor_id:
                templates = self.env["shop.bill.ocr.template"].ensure_for_vendor(
                    rec.company_id, rec.vendor_id
                )
                active_templates = templates.filtered("active")
                if not rec.ocr_template_id and len(active_templates) == 1:
                    rec.ocr_template_id = active_templates
        return records

    def write(self, vals):
        if vals.get("vendor_id"):
            self.env["res.partner"].browse(vals["vendor_id"])._mark_as_shop_vendor()
        result = super().write(vals)
        if {"vendor_id", "company_id"}.intersection(vals):
            for rec in self.filtered("vendor_id"):
                self.env["shop.bill.ocr.template"].ensure_for_vendor(
                    rec.company_id, rec.vendor_id
                )
                templates = self.env["shop.bill.ocr.template"].search([
                    ("company_id", "=", rec.company_id.id),
                    ("partner_id", "=", rec.vendor_id.id),
                    ("active", "=", True),
                ])
                if rec.ocr_template_id not in templates:
                    rec.ocr_template_id = templates if len(templates) == 1 else False
                for line in rec.line_ids.filtered("product_id"):
                    line._create_or_update_supplierinfo(line.product_id)
        return result

    @api.constrains("bill_number", "vendor_id", "company_id")
    def _check_duplicate_bill(self):
        for rec in self:
            if not rec.bill_number or not rec.vendor_id:
                continue
            duplicate = self.search_count([
                ("id", "!=", rec.id),
                ("company_id", "=", rec.company_id.id),
                ("vendor_id", "=", rec.vendor_id.id),
                ("bill_number", "=", rec.bill_number),
                ("state", "!=", "cancelled"),
            ])
            if duplicate:
                raise ValidationError(_("This vendor bill number has already been imported."))

    def action_start_review(self):
        self.write({"state": "review"})
        return True

    def _get_bill_mimetype(self, file_bytes, file_name=None):
        self.ensure_one()
        if file_bytes.startswith(b"%PDF"):
            return "application/pdf"
        if file_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if file_bytes.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if file_bytes.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif"
        if file_bytes.startswith(b"RIFF") and file_bytes[8:12] == b"WEBP":
            return "image/webp"
        guessed_type = mimetypes.guess_type(
            (file_name or "") if file_name is not None else (self.original_file_name or "")
        )[0]
        return guessed_type if guessed_type in SUPPORTED_BILL_MIMETYPES else False

    def _decode_bill_file(self, binary_value, file_name, file_label):
        self.ensure_one()
        if not binary_value:
            raise UserError(_("Upload a file for %(label)s.", label=file_label))
        try:
            encoded_file = (
                binary_value.decode("ascii")
                if isinstance(binary_value, bytes)
                else binary_value
            )
            file_bytes = base64.b64decode(encoded_file, validate=True)
        except (UnicodeDecodeError, ValueError) as error:
            raise UserError(
                _("%(label)s is not a valid file.", label=file_label)
            ) from error
        if not file_bytes:
            raise UserError(_("%(label)s is empty.", label=file_label))
        if len(file_bytes) > MAX_BILL_FILE_SIZE:
            raise UserError(
                _("Each bill page or PDF must be 20 MB or smaller.")
            )
        mimetype = self._get_bill_mimetype(file_bytes, file_name=file_name)
        if not mimetype:
            raise UserError(
                _("Use a PDF, JPEG, PNG, WebP, or GIF file for %(label)s.", label=file_label)
            )
        return file_bytes, mimetype

    def _crop_bill_image(self, file_bytes, mimetype, enabled, crop_values):
        self.ensure_one()
        if not enabled:
            return file_bytes, mimetype
        if not mimetype.startswith("image/"):
            raise UserError(_("Manual crop is available only for image bills, not PDFs."))
        left, top, right, bottom = crop_values
        if not (0 <= left < right <= 100 and 0 <= top < bottom <= 100):
            raise UserError(_("Draw a valid crop area on the bill preview."))
        if right - left < 5 or bottom - top < 5:
            raise UserError(_("The crop area is too small. Select at least 5% of the image."))
        try:
            from PIL import Image
            with Image.open(BytesIO(file_bytes)) as source:
                source.load()
                width, height = source.size
                box = (
                    round(width * left / 100), round(height * top / 100),
                    round(width * right / 100), round(height * bottom / 100),
                )
                cropped = source.crop(box)
                output = BytesIO()
                if cropped.mode not in ("RGB", "L"):
                    cropped = cropped.convert("RGB")
                cropped.save(output, format="PNG", optimize=True)
                return output.getvalue(), "image/png"
        except UserError:
            raise
        except Exception as error:
            raise UserError(_("The selected bill crop could not be created.")) from error

    def _prepare_bill_file(self):
        self.ensure_one()
        if not self.original_file:
            raise UserError(_("Upload a bill image or PDF before extracting it."))
        file_bytes, mimetype = self._decode_bill_file(
            self.original_file,
            self.original_file_name,
            _("the first bill page"),
        )
        return self._apply_manual_crop(file_bytes, mimetype)

    def _prepare_bill_documents(self):
        """Return ordered, cropped source documents for one logical bill."""
        self.ensure_one()
        documents = []
        if self.original_file:
            file_bytes, mimetype = self._prepare_bill_file()
            documents.append((file_bytes, mimetype, self.original_file_name or "page-1"))
        for index, page in enumerate(self.page_ids.sorted(lambda item: (item.sequence, item.id)), start=2):
            file_bytes, mimetype = page._prepare_page_file()
            documents.append((file_bytes, mimetype, page.page_file_name or "page-%s" % index))
        if not documents:
            raise UserError(_("Upload at least one bill image or PDF before extracting."))
        if len(documents) > MAX_BILL_DOCUMENTS:
            raise UserError(_("A bill can contain at most %(count)s uploaded files.", count=MAX_BILL_DOCUMENTS))
        if sum(len(document[0]) for document in documents) > MAX_BILL_TOTAL_SIZE:
            raise UserError(_("All bill pages together must be 100 MB or smaller."))
        return documents

    def _prepare_combined_bill(self):
        """Combine images/PDFs so the OCR parser sees one ordered multi-page bill."""
        self.ensure_one()
        documents = self._prepare_bill_documents()
        if len(documents) == 1:
            return documents[0][0], documents[0][1]
        try:
            import fitz

            combined = fitz.open()
            for file_bytes, mimetype, _file_name in documents:
                source = fitz.open(stream=file_bytes, filetype=mimetype.split("/", 1)[1])
                try:
                    if mimetype == "application/pdf":
                        combined.insert_pdf(source)
                    else:
                        image_pdf = fitz.open("pdf", source.convert_to_pdf())
                        try:
                            combined.insert_pdf(image_pdf)
                        finally:
                            image_pdf.close()
                finally:
                    source.close()
            if not combined.page_count:
                raise UserError(_("The uploaded bill pages could not be combined."))
            combined_bytes = combined.tobytes(garbage=4, deflate=True)
            combined.close()
            return combined_bytes, "application/pdf"
        except UserError:
            raise
        except Exception as error:
            raise UserError(_("The uploaded bill pages could not be combined.")) from error

    def _extract_bill_locally(self, file_bytes, mimetype):
        if not self.vendor_id:
            raise UserError(_("Select the vendor before extracting the bill so its OCR template can be used."))
        templates = self.env["shop.bill.ocr.template"].ensure_for_vendor(
            self.company_id, self.vendor_id
        ).filtered("active")
        template = self.ocr_template_id
        if template and template not in templates:
            template = False
        if not template and len(templates) == 1:
            template = templates
            self.ocr_template_id = template
        if not template:
            raise UserError(_(
                "This vendor has multiple bill layouts. Select the Bill OCR Template before extracting."
            ))
        try:
            return extract_bill(
                file_bytes,
                mimetype,
                template_config=template.get_extraction_config(),
            )
        except LocalOCRError as error:
            raise UserError(_("Local bill extraction failed: %s", str(error))) from error
        except Exception as error:
            _logger.exception("Unexpected local bill OCR failure")
            raise UserError(
                _("Local bill extraction failed unexpectedly. Check the Odoo log for details.")
            ) from error

    def _find_extracted_vendor(self, extracted_data):
        self.ensure_one()
        Partner = self.env["res.partner"]
        vendor_tax_id = (extracted_data.get("vendor_tax_id") or "").strip()
        if vendor_tax_id:
            vendor = Partner.search([
                ("shop_is_vendor", "=", True),
                ("vat", "=ilike", vendor_tax_id),
            ], limit=1)
            if vendor:
                return vendor
        vendor_name = (extracted_data.get("vendor_name") or "").strip()
        if not vendor_name:
            return Partner
        vendor = Partner.search([
            ("shop_is_vendor", "=", True),
            ("name", "=ilike", vendor_name),
        ], limit=1)
        if vendor:
            return vendor
        vendor = Partner.search([
            ("shop_is_vendor", "=", True),
            ("name", "ilike", vendor_name),
        ], limit=1)
        if vendor:
            return vendor

        # OCR commonly confuses one or two letters in a company suffix (for
        # example AGENCIES / AGENCTES).  A conservative local similarity pass
        # keeps explicitly marked vendors matchable despite small OCR errors.
        normalised_name = re.sub(r"[^A-Z0-9]", "", vendor_name.upper())
        candidates = Partner.with_context(active_test=False).search(
            [("shop_is_vendor", "=", True), ("name", "!=", False)], limit=2000
        )
        best_vendor = Partner
        best_ratio = 0.0
        for candidate in candidates:
            candidate_name = re.sub(r"[^A-Z0-9]", "", candidate.name.upper())
            ratio = SequenceMatcher(None, normalised_name, candidate_name).ratio()
            if ratio > best_ratio:
                best_vendor, best_ratio = candidate, ratio
        return best_vendor if best_ratio >= 0.78 else Partner

    def _find_purchase_tax(self, gst_percent):
        self.ensure_one()
        if not gst_percent:
            return self.env["account.tax"]
        Tax = self.env["account.tax"]
        domain = [
            ("company_id", "=", self.company_id.id),
            ("type_tax_use", "=", "purchase"),
            ("active", "=", True),
        ]
        taxes = Tax.search(domain)

        # Match the numeric rate, never a partial name such as "5%". In an
        # SQL LIKE expression the percent sign is a wildcard, which previously
        # allowed an extracted 5% rate to select a tax named 15%.
        for tax in taxes.filtered(lambda candidate: candidate.amount_type == "percent"):
            if float_compare(tax.amount, gst_percent, precision_digits=4) == 0:
                return tax

        # Indian GST is commonly configured as a group (for example, CGST
        # 2.5% + SGST 2.5%). Match such a group by the sum of its percentage
        # children while rejecting fixed/division tax groups.
        for tax in taxes.filtered(lambda candidate: candidate.amount_type == "group"):
            children = tax.children_tax_ids
            if (
                children
                and all(child.amount_type == "percent" for child in children)
                and float_compare(
                    sum(children.mapped("amount")), gst_percent, precision_digits=4
                ) == 0
            ):
                return tax

        return Tax

    def action_match_taxes(self, preserve_existing=False):
        missing_rates = set()
        for rec in self:
            for line in rec.line_ids.filtered(lambda candidate: candidate.gst_percent):
                if preserve_existing and line.tax_ids:
                    continue
                tax = rec._find_purchase_tax(line.gst_percent)
                line.tax_ids = tax
                if not tax:
                    missing_rates.add(line.gst_percent)
            rec.state = "review"
        if missing_rates:
            rates = ", ".join("%g%%" % rate for rate in sorted(missing_rates))
            message = _(
                "No exact purchase tax is configured for %(rates)s. "
                "Those lines were left blank instead of assigning an incorrect tax.",
                rates=rates,
            )
            notification_type = "warning"
        else:
            message = _("Every extracted GST rate was matched to an exact purchase tax.")
            notification_type = "success"
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Tax matching complete"),
                "message": message,
                "type": notification_type,
                "sticky": bool(missing_rates),
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }

    def _line_values_from_extraction(self, extracted_line, sequence):
        self.ensure_one()
        description = (extracted_line.get("description") or "").strip()
        if not description:
            return False
        gst_percent = max(float(extracted_line.get("gst_percent") or 0.0), 0.0)
        tax = self._find_purchase_tax(gst_percent)
        return {
            "sequence": sequence,
            "raw_description": description,
            "corrected_description": description,
            "vendor_product_code": extracted_line.get("vendor_product_code") or False,
            "barcode": extracted_line.get("barcode") or False,
            "hsn_code": extracted_line.get("hsn_code") or False,
            "quantity": max(float(extracted_line.get("quantity") or 0.0), 0.0),
            "free_quantity": max(float(extracted_line.get("free_quantity") or 0.0), 0.0),
            "purchase_rate": max(float(extracted_line.get("purchase_rate") or 0.0), 0.0),
            "discount_percent": min(max(float(extracted_line.get("discount_percent") or 0.0), 0.0), 100.0),
            "gst_percent": gst_percent,
            "tax_ids": [(6, 0, tax.ids)],
            "extraction_confidence": min(max(float(extracted_line.get("confidence") or 0.0), 0.0), 100.0),
        }

    def _apply_extracted_bill(self, extracted_data, model, response_id):
        self.ensure_one()
        extracted_lines = extracted_data.get("lines") or []
        line_commands = []
        for index, extracted_line in enumerate(extracted_lines, start=1):
            try:
                line_values = self._line_values_from_extraction(extracted_line, index * 10)
            except (TypeError, ValueError) as error:
                raise UserError(_("A bill line contains an invalid number.")) from error
            if line_values:
                line_commands.append((0, 0, line_values))
        if not line_commands:
            raise UserError(_("No product lines were found in the uploaded bill."))

        values = {
            "line_ids": line_commands,
            "raw_extracted_text": json.dumps(extracted_data, indent=2, ensure_ascii=False),
            "extracted_vendor_name": extracted_data.get("vendor_name") or False,
            "extraction_status": "done",
            "extraction_model": model,
            "extraction_response_id": response_id or False,
            "extracted_at": fields.Datetime.now(),
            "state": "review",
        }
        if not self.vendor_id:
            vendor = self._find_extracted_vendor(extracted_data)
            if vendor:
                values["vendor_id"] = vendor.id
        if extracted_data.get("bill_number"):
            values["bill_number"] = extracted_data["bill_number"].strip()
        if extracted_data.get("bill_date"):
            try:
                values["bill_date"] = fields.Date.to_date(extracted_data["bill_date"])
            except (TypeError, ValueError):
                pass
        currency_code = (extracted_data.get("currency_code") or "").strip().upper()
        if currency_code:
            currency = self.env["res.currency"].search([("name", "=", currency_code)], limit=1)
            if currency:
                values["currency_id"] = currency.id
        if extracted_data.get("notes"):
            values["note"] = extracted_data["notes"]

        self.write(values)
        self.action_match_products()

    def action_extract_bill(self):
        self.ensure_one()
        if self.state not in ("draft", "review"):
            raise UserError(_("Only uploaded or review-stage bills can be extracted."))
        if self.line_ids:
            raise UserError(_("Remove the existing bill lines before extracting the file again."))

        file_bytes, mimetype = self._prepare_combined_bill()
        try:
            extracted_data, engine, fingerprint = self._extract_bill_locally(
                file_bytes, mimetype
            )
            self._apply_extracted_bill(extracted_data, engine, fingerprint)
        except UserError:
            self.extraction_status = "failed"
            raise

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Bill extracted"),
                "message": _("Review the vendor, matched products, quantities, rates, and taxes before continuing."),
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }

    def action_match_products(self):
        Product = self.env["product.product"]
        Mapping = self.env["shop.vendor.product.map"]
        BillNameMapping = self.env["shop.bill.product.name.map"]

        for rec in self:
            # Also backfill connections for lines matched before this feature
            # was installed.
            rec.line_ids.filtered("product_id")._connect_bill_name_to_product()
            for line in rec.line_ids.filtered(lambda l: not l.product_id):
                product = False

                if line.barcode:
                    product = Product.search([("barcode", "=", line.barcode)], limit=1)

                if not product and line.vendor_product_code:
                    mapping = Mapping.search([
                        ("company_id", "=", rec.company_id.id),
                        ("partner_id", "=", rec.vendor_id.id),
                        ("vendor_product_code", "=", line.vendor_product_code),
                    ], limit=1)
                    product = mapping.product_id

                if not product and line.barcode:
                    mapping = Mapping.search([
                        ("company_id", "=", rec.company_id.id),
                        ("partner_id", "=", rec.vendor_id.id),
                        ("barcode", "=", line.barcode),
                    ], limit=1)
                    product = mapping.product_id

                if not product and line.raw_description:
                    mapping = Mapping.search([
                        ("company_id", "=", rec.company_id.id),
                        ("partner_id", "=", rec.vendor_id.id),
                        ("vendor_product_name", "=ilike", line.raw_description.strip()),
                    ], limit=1)
                    product = mapping.product_id

                if not product and line.raw_description:
                    product = BillNameMapping.find_product(
                        rec.company_id, line.raw_description
                    )

                if not product and line.raw_description:
                    product = Product.search([
                        ("purchase_ok", "=", True),
                        ("name", "=ilike", line.raw_description.strip()),
                    ], limit=1)

                if product:
                    line.write({
                        "product_id": product.id,
                        "corrected_description": product.name,
                    })

            rec.state = "review"
        return True

    def action_create_missing_products(self):
        created_count = 0
        for rec in self:
            if rec.state not in ("draft", "review"):
                raise UserError(_("Products can only be created while reviewing the bill import."))
            if not rec.vendor_id:
                raise UserError(
                    _("Select the vendor before creating and matching products.")
                )

            # Give barcode, vendor mapping and exact-name matching one final
            # chance before creating anything new.
            rec.action_match_products()
            for line in rec.line_ids.sorted("sequence"):
                if line.product_id:
                    continue
                line._create_standalone_product()
                created_count += 1
                # A later duplicate line can now match the product just made.
                rec.action_match_products()

            rec.state = "review"

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Products created"),
                "message": _(
                    "Created %(count)s real stock product(s) and matched them to the bill.",
                    count=created_count,
                ),
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }

    def action_mark_ready(self):
        for rec in self:
            if not rec.vendor_id:
                raise UserError(_("Select the vendor before marking this import ready."))
            if not rec.line_ids:
                raise UserError(_("Add at least one bill line before marking this import ready."))
            if rec.line_ids.filtered(lambda l: not l.product_id):
                raise UserError(_("Match every bill line to an Odoo product first."))
            missing_tax_lines = rec.line_ids.filtered(lambda l: l.gst_percent and not l.tax_ids)
            if missing_tax_lines:
                raise UserError(
                    _("Select a purchase tax for these bill lines before creating the purchase order: %(lines)s",
                      lines=", ".join("%s (%g%%)" % (line.raw_description, line.gst_percent)
                                      for line in missing_tax_lines))
                )
            rec.state = "ready"
        return True

    def _create_or_update_vendor_mapping(self, line):
        if not line.raw_description or not line.product_id:
            return
        if not self.vendor_id:
            raise UserError(
                _("Select the vendor before creating a vendor product mapping.")
            )
        Mapping = self.env["shop.vendor.product.map"]
        domain = [
            ("company_id", "=", self.company_id.id),
            ("partner_id", "=", self.vendor_id.id),
            ("vendor_product_name", "=ilike", line.raw_description.strip()),
        ]
        mapping = Mapping.search(domain, limit=1)
        vals = {
            "company_id": self.company_id.id,
            "partner_id": self.vendor_id.id,
            "vendor_product_name": line.raw_description.strip(),
            "vendor_product_code": line.vendor_product_code or False,
            "barcode": line.barcode or False,
            "product_id": line.product_id.id,
        }
        if mapping:
            mapping.write(vals)
        else:
            Mapping.create(vals)

    def action_create_purchase_order(self):
        PurchaseOrder = self.env["purchase.order"]

        for rec in self:
            if rec.state != "ready":
                raise UserError(_("The bill import must be in Ready state before creating a purchase order."))
            if rec.purchase_order_id:
                raise UserError(_("A purchase order has already been created for this import."))

            order = PurchaseOrder.create({
                "partner_id": rec.vendor_id.id,
                "company_id": rec.company_id.id,
                "currency_id": rec.currency_id.id,
                "date_order": fields.Datetime.now(),
                "partner_ref": rec.bill_number or rec.name,
                "origin": rec.name,
                "shop_purchase_import_id": rec.id,
            })

            rec._create_lines_on_purchase_order(order)

            rec.write({
                "purchase_order_id": order.id,
                "state": "po_created",
            })

        return {
            "type": "ir.actions.act_window",
            "res_model": "purchase.order",
            "view_mode": "form",
            "res_id": self.purchase_order_id.id if len(self) == 1 else False,
        }

    def action_create_reviewed_purchase_order(self):
        """Validate the reviewed extraction and create its PO in one action."""
        self.ensure_one()
        if self.state not in ("draft", "review", "ready"):
            raise UserError(_("Only an uploaded, review, or ready bill can create a purchase order."))
        if self.purchase_order_id:
            raise UserError(_("A purchase order has already been created for this import."))
        if self.state != "ready":
            self.action_match_products()
            if self.unmatched_line_count:
                raise UserError(_(
                    "Match or create the remaining %(count)s product(s), review their names, "
                    "then click Create Reviewed PO again.",
                    count=self.unmatched_line_count,
                ))
            # The user has reviewed these selections; OCR must not replace them.
            self.action_match_taxes(preserve_existing=True)
            self.action_mark_ready()
        return self.action_create_purchase_order()

    def _create_lines_on_purchase_order(self, order):
        self.ensure_one()
        PurchaseOrderLine = self.env["purchase.order.line"]
        PriceHistory = self.env["shop.product.price.history"]

        for line in self.line_ids:
            qty = line.quantity or 0.0
            effective_price = line._effective_purchase_price()
            base_vals = {
                "order_id": order.id,
                "product_id": line.product_id.id,
                "name": line.raw_description or line.product_id.display_name,
                "product_qty": qty,
                "product_uom_id": (line.uom_id or line.product_id.uom_id).id,
                "price_unit": effective_price,
                "date_planned": fields.Datetime.now(),
                "tax_ids": [(6, 0, line.tax_ids.ids)],
            }
            if qty:
                PurchaseOrderLine.create(base_vals)

            if line.free_quantity:
                free_vals = dict(base_vals)
                free_vals.update({
                    "name": _("%s (Free Qty)") % (
                        line.raw_description or line.product_id.display_name
                    ),
                    "product_qty": line.free_quantity,
                    "price_unit": 0.0,
                })
                PurchaseOrderLine.create(free_vals)

            self._create_or_update_vendor_mapping(line)
            line._connect_bill_name_to_product()
            line._create_or_update_supplierinfo(line.product_id)
            line._sync_product_cost(line.product_id)

            PriceHistory.create({
                "company_id": self.company_id.id,
                "product_id": line.product_id.id,
                "partner_id": self.vendor_id.id,
                "purchase_import_id": self.id,
                "purchase_order_id": order.id,
                "purchase_date": self.bill_date,
                "currency_id": self.currency_id.id,
                "qty": line.quantity,
                "unit_price": effective_price,
                "source_description": line.raw_description,
            })

    def action_reset_to_review(self):
        for rec in self:
            if rec.purchase_order_id:
                if rec.purchase_order_id.state not in ("draft", "sent", "cancel"):
                    raise UserError(_("You cannot reset this import because its purchase order is already confirmed."))
                if rec.purchase_order_id.state != "cancel":
                    rec.purchase_order_id.button_cancel()
                rec.purchase_order_id = False
            rec.state = "review"
        return True

    def action_revert_purchase_workflow(self):
        """Safely undo the PO, draft bill, and received stock for this import."""
        self.ensure_one()
        if not self.purchase_order_id:
            raise UserError(_("This bill import has no purchase order to revert."))
        return self.purchase_order_id.action_shop_revert_import()

    def action_cancel(self):
        self.write({"state": "cancelled"})
        return True


class ShopPurchaseImportPage(models.Model):
    _name = "shop.purchase.import.page"
    _description = "Purchase Bill Continuation Page"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    import_id = fields.Many2one(
        "shop.purchase.import",
        required=True,
        ondelete="cascade",
        index=True,
    )
    page_file = fields.Binary(string="Page Image/PDF", required=True, attachment=True)
    page_file_name = fields.Char(string="File Name", required=True)
    is_image = fields.Boolean(compute="_compute_is_image")
    manual_crop_enabled = fields.Boolean(string="Use Manual Crop", copy=False)
    crop_left = fields.Float(default=0.0, copy=False)
    crop_top = fields.Float(default=0.0, copy=False)
    crop_right = fields.Float(default=100.0, copy=False)
    crop_bottom = fields.Float(default=100.0, copy=False)

    @api.depends("page_file", "page_file_name")
    def _compute_is_image(self):
        for page in self:
            page.is_image = bool(page.page_file) and not (
                (page.page_file_name or "").lower().endswith(".pdf")
            )

    @api.onchange("page_file")
    def _onchange_page_file_reset_crop(self):
        self.manual_crop_enabled = False
        self.crop_left, self.crop_top = 0.0, 0.0
        self.crop_right, self.crop_bottom = 100.0, 100.0

    def action_reset_manual_crop(self):
        self.ensure_one()
        self.write({
            "manual_crop_enabled": False,
            "crop_left": 0.0,
            "crop_top": 0.0,
            "crop_right": 100.0,
            "crop_bottom": 100.0,
        })
        return True

    def _prepare_page_file(self):
        self.ensure_one()
        file_bytes, mimetype = self.import_id._decode_bill_file(
            self.page_file,
            self.page_file_name,
            _("continuation page %(page)s", page=self.sequence),
        )
        return self.import_id._crop_bill_image(
            file_bytes,
            mimetype,
            self.manual_crop_enabled,
            (self.crop_left, self.crop_top, self.crop_right, self.crop_bottom),
        )


class ShopPurchaseImportLine(models.Model):
    _name = "shop.purchase.import.line"
    _description = "Purchase Bill Import Line"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    import_id = fields.Many2one("shop.purchase.import", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="import_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="import_id.currency_id", store=True)
    vendor_id = fields.Many2one(related="import_id.vendor_id", store=True, index=True)

    raw_description = fields.Char(string="Original Bill Description", required=True)
    corrected_description = fields.Char(
        string="POS Product Name",
        help="Simple product name shown in Odoo and the Point of Sale while preserving the supplier's original description.",
    )
    vendor_product_code = fields.Char(index=True)
    barcode = fields.Char(index=True)
    product_id = fields.Many2one(
        "product.product",
        string="Matched Product",
        domain="[('purchase_ok', '=', True)]",
        index=True,
    )
    uom_id = fields.Many2one("uom.uom", string="UoM")
    hsn_code = fields.Char(string="HSN/SAC")
    gst_percent = fields.Float(
        string="Extracted GST %",
        help="GST percentage read from the supplier bill. Select the matching Odoo purchase tax before approval.",
    )
    quantity = fields.Float(default=1.0)
    free_quantity = fields.Float(default=0.0)
    purchase_rate = fields.Float(digits=(16, 6))
    discount_percent = fields.Float(string="Discount %", default=0.0)
    units_per_purchase_qty = fields.Float(
        string="Units in One Qty",
        default=1.0,
        help=(
            "Number of individual sale units contained in one purchased quantity. "
            "This divides the tax-inclusive purchase cost to set the product variant cost and "
            "does not change the purchase rate or purchase-order quantity."
        ),
    )
    tax_ids = fields.Many2many(
        "account.tax",
        "shop_purchase_import_line_tax_rel",
        "line_id",
        "tax_id",
        string="Taxes",
        domain="[('type_tax_use', '=', 'purchase'), ('company_id', '=', company_id)]",
    )
    extraction_confidence = fields.Float(
        string="Extraction Confidence %",
        help="Optional confidence supplied by OCR/document extraction.",
    )

    taxable_amount = fields.Monetary(compute="_compute_amounts", store=True, currency_field="currency_id")
    tax_amount = fields.Monetary(compute="_compute_amounts", store=True, currency_field="currency_id")
    total_amount = fields.Monetary(
        string="Total Cost (Incl. Tax)",
        compute="_compute_amounts",
        inverse="_inverse_total_amount",
        store=True,
        currency_field="currency_id",
        help="Total line cost including tax. Editing it adjusts the purchase rate while keeping quantity, discount and taxes.",
    )
    purchase_unit_price_incl_tax = fields.Monetary(
        string="Unit Price (Incl. Tax)",
        compute="_compute_amounts",
        store=True,
        currency_field="currency_id",
        help="Cost of one purchased quantity after discount and including tax.",
    )
    item_unit_cost_incl_tax = fields.Monetary(
        string="Per-Item Cost (Incl. Tax)",
        compute="_compute_amounts",
        store=True,
        currency_field="currency_id",
        help=(
            "Unit Price (Incl. Tax) divided by Units in One Qty. This value is "
            "used for the product variant Cost and does not change the purchase rate."
        ),
    )

    def _connect_bill_name_to_product(self):
        Mapping = self.env["shop.bill.product.name.map"]
        for line in self:
            if line.raw_description and line.product_id and line.company_id:
                Mapping.connect(
                    line.company_id,
                    line.raw_description,
                    line.product_id,
                )

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines._connect_bill_name_to_product()
        for line in lines.filtered(lambda item: item.product_id and item.vendor_id):
            line._create_or_update_supplierinfo(line.product_id)
        return lines

    def write(self, vals):
        result = super().write(vals)
        if {"raw_description", "product_id", "import_id"}.intersection(vals):
            self._connect_bill_name_to_product()
        if {
            "raw_description", "product_id", "uom_id", "vendor_product_code",
            "purchase_rate", "discount_percent", "import_id",
        }.intersection(vals):
            for line in self.filtered(lambda item: item.product_id and item.vendor_id):
                line._create_or_update_supplierinfo(line.product_id)
        return result

    def _effective_purchase_price(self):
        self.ensure_one()
        return (self.purchase_rate or 0.0) * (
            1.0 - (self.discount_percent or 0.0) / 100.0
        )

    def _initial_cost_in_company_currency(self):
        self.ensure_one()
        cost = self.item_unit_cost_incl_tax
        company_currency = self.company_id.currency_id
        if self.currency_id and self.currency_id != company_currency:
            cost = self.currency_id._convert(
                cost,
                company_currency,
                self.company_id,
                self.import_id.bill_date or fields.Date.context_today(self),
            )
        return cost

    def _check_product_barcode(self, product=False, barcode=False):
        self.ensure_one()
        barcode = (barcode or self.barcode or "").strip()
        if not barcode:
            return
        domain = [("barcode", "=", barcode)]
        if product:
            domain.append(("id", "!=", product.id))
        duplicate = self.env["product.product"].with_context(active_test=False).search(
            domain, limit=1
        )
        if duplicate:
            raise UserError(
                _(
                    "Barcode %(barcode)s already belongs to %(product)s.",
                    barcode=barcode,
                    product=duplicate.display_name,
                )
            )

    def _create_or_update_supplierinfo(self, product):
        self.ensure_one()
        if not self.vendor_id:
            return
        SupplierInfo = self.env["product.supplierinfo"]
        domain = [
            ("partner_id", "=", self.vendor_id.id),
            ("product_id", "=", product.id),
            ("company_id", "=", self.company_id.id),
        ]
        supplierinfo = SupplierInfo.search(domain, limit=1)
        values = {
            "partner_id": self.vendor_id.id,
            "product_id": product.id,
            "product_tmpl_id": product.product_tmpl_id.id,
            "company_id": self.company_id.id,
            "currency_id": self.currency_id.id,
            "product_name": self.raw_description,
            "product_code": self.vendor_product_code or False,
            "product_uom_id": (self.uom_id or product.uom_id).id,
            "min_qty": 0.0,
            "price": self.purchase_rate,
            "discount": self.discount_percent,
            "date_start": self.import_id.bill_date or False,
        }
        if supplierinfo:
            supplierinfo.write(values)
        else:
            SupplierInfo.create(values)

    def _sync_product_cost(self, product):
        """Keep the product cost aligned with the reviewed supplier-bill cost."""
        self.ensure_one()
        if not product:
            return
        product.with_company(self.company_id).standard_price = (
            self._initial_cost_in_company_currency()
        )

    def _attach_created_product(
        self,
        product,
        standard_price=None,
        barcode=False,
        default_code=False,
        box_quantity=1.0,
        hsn_code=False,
    ):
        self.ensure_one()
        self._check_product_barcode(product=product, barcode=barcode)
        product_values = {
            "standard_price": (
                self._initial_cost_in_company_currency()
                if standard_price is None
                else standard_price
            ),
            "shop_box_qty": box_quantity or 1.0,
        }
        if barcode:
            product_values["barcode"] = barcode.strip()
        if default_code:
            product_values["default_code"] = default_code.strip()
        product.with_company(self.company_id).write(product_values)
        if hsn_code:
            product.product_tmpl_id.shop_hsn_code = hsn_code.strip()

        self.write({
            "product_id": product.id,
            "uom_id": (self.uom_id or product.uom_id).id,
        })
        self.import_id._create_or_update_vendor_mapping(self)
        self._create_or_update_supplierinfo(product)
        return product

    def _create_standalone_product(self):
        self.ensure_one()
        if self.product_id:
            return self.product_id
        self._check_product_barcode()
        name = (self.corrected_description or self.raw_description or "").strip()
        if not name:
            raise UserError(_("Enter a product description before creating the product."))
        values = {
            "name": name,
            "type": "consu",
            "is_storable": True,
            "sale_ok": True,
            "purchase_ok": True,
            "available_in_pos": True,
            "company_id": self.company_id.id,
            "description_purchase": self.raw_description,
            "shop_hsn_code": self.hsn_code or False,
            "supplier_taxes_id": [(6, 0, self.tax_ids.ids)],
        }
        product = self.env["product.product"].with_company(self.company_id).create(values)
        return self._attach_created_product(
            product,
            barcode=self.barcode,
            default_code=self.vendor_product_code,
            hsn_code=self.hsn_code,
        )

    def action_open_create_product(self):
        self.ensure_one()
        if self.product_id:
            raise UserError(_("This bill line is already matched to a product."))
        if self.import_id.state not in ("draft", "review"):
            raise UserError(_("Products can only be created while reviewing the bill import."))
        return {
            "name": _("Create Product or Variant"),
            "type": "ir.actions.act_window",
            "res_model": "shop.purchase.import.create.product.wizard",
            "view_mode": "form",
            "view_id": self.env.ref(
                "wholesale_shop_pos.view_shop_purchase_import_create_product_wizard_form"
            ).id,
            "target": "new",
            "context": {
                "default_line_id": self.id,
                "default_vendor_id": self.vendor_id.id,
                "default_product_name": (
                    self.corrected_description or self.raw_description
                ),
                "default_default_code": self.vendor_product_code,
                "default_barcode": self.barcode,
                "default_hsn_code": self.hsn_code,
                "default_standard_price": self._initial_cost_in_company_currency(),
                "default_uom_id": (self.uom_id or self.env.ref("uom.product_uom_unit")).id,
            },
        }

    @api.onchange("product_id")
    def _onchange_product_id(self):
        for line in self:
            if line.product_id:
                line.uom_id = line.product_id.uom_id
                if (
                    not line.corrected_description
                    or line.corrected_description == line.raw_description
                ):
                    line.corrected_description = line.product_id.name
                if not line.barcode:
                    line.barcode = line.product_id.barcode
                if not line.tax_ids:
                    line.tax_ids = line.product_id.supplier_taxes_id.filtered(
                        lambda t: t.company_id == line.company_id
                    )

    def _inverse_total_amount(self):
        for line in self:
            target = line.total_amount
            if line.import_id.state not in ("draft", "review"):
                raise ValidationError(_("Only uploaded or review bill totals can be edited."))
            if target < 0:
                raise ValidationError(_("Line total cannot be negative."))
            discount_factor = 1 - line.discount_percent / 100.0
            if line.quantity <= 0 or discount_factor <= 0:
                raise ValidationError(_("Set a positive quantity and a discount below 100% before editing the total."))

            def total_at(rate, raw=False):
                if not line.tax_ids:
                    return rate * discount_factor * line.quantity
                return line.tax_ids.with_context(round_base=not raw).compute_all(
                    rate * discount_factor, currency=line.currency_id,
                    quantity=line.quantity, product=line.product_id,
                    partner=line.vendor_id,
                    rounding_method="round_globally" if raw else None,
                )["total_included"]

            # Two positive samples include fixed, grouped and price-included
            # taxes without treating every tax as a simple percentage.
            first, second = total_at(1, True), total_at(2, True)
            slope = second - first
            if slope <= 0:
                raise ValidationError(_("These taxes do not support adjusting the total. Edit the purchase rate instead."))
            rate = max(0, (target - (first - slope)) / slope)
            low, high = 0, max(1, rate * 2)
            while total_at(high) < target and high < 1e12:
                high *= 2
            for attempt in range(60):
                actual = total_at(rate)
                if line.currency_id.compare_amounts(actual, target) == 0:
                    break
                if actual < target:
                    low = rate
                else:
                    high = rate
                rate = (low + high) / 2
            else:
                raise ValidationError(_("This total cannot be reached with the selected quantity, discount and taxes."))
            line.purchase_rate = rate
            line._compute_amounts()

    @api.depends(
        "quantity",
        "purchase_rate",
        "discount_percent",
        "tax_ids",
        "currency_id",
        "product_id",
        "vendor_id",
        "units_per_purchase_qty",
    )
    def _compute_amounts(self):
        for line in self:
            qty = line.quantity or 0.0
            discounted_unit = (line.purchase_rate or 0.0) * (1.0 - (line.discount_percent or 0.0) / 100.0)
            if line.tax_ids:
                unit_taxes = line.tax_ids.compute_all(
                    discounted_unit,
                    currency=line.currency_id,
                    quantity=1.0,
                    product=line.product_id,
                    partner=line.vendor_id,
                )
                taxes = line.tax_ids.compute_all(
                    discounted_unit,
                    currency=line.currency_id,
                    quantity=qty,
                    product=line.product_id,
                    partner=line.vendor_id,
                )
                line.taxable_amount = taxes.get("total_excluded", 0.0)
                line.total_amount = taxes.get("total_included", 0.0)
                line.tax_amount = line.total_amount - line.taxable_amount
                line.purchase_unit_price_incl_tax = unit_taxes.get(
                    "total_included", 0.0
                )
            else:
                line.taxable_amount = discounted_unit * qty
                line.tax_amount = 0.0
                line.total_amount = line.taxable_amount
                line.purchase_unit_price_incl_tax = discounted_unit
            divisor = line.units_per_purchase_qty or 0.0
            line.item_unit_cost_incl_tax = (
                line.purchase_unit_price_incl_tax / divisor if divisor > 0 else 0.0
            )

    @api.constrains(
        "quantity", "free_quantity", "discount_percent", "units_per_purchase_qty"
    )
    def _check_values(self):
        for line in self:
            if line.quantity < 0 or line.free_quantity < 0:
                raise ValidationError(_("Quantity and free quantity cannot be negative."))
            if line.discount_percent < 0 or line.discount_percent > 100:
                raise ValidationError(_("Discount must be between 0 and 100 percent."))
            if line.units_per_purchase_qty <= 0:
                raise ValidationError(_("Units in One Qty must be greater than zero."))
