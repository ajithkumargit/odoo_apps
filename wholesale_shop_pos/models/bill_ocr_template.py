import json
import unicodedata

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


HEADER_ROLES = [
    ("bill_number", "Bill / Invoice Number"),
    ("bill_date", "Bill / Invoice Date"),
    ("serial", "Serial Number"),
    ("description", "Product Description"),
    ("hsn", "HSN/SAC"),
    ("uom", "Unit of Measure"),
    ("secondary_quantity", "Secondary Quantity / Weight"),
    ("barcode", "Barcode"),
    ("upc", "Units per Case"),
    ("mrp", "MRP"),
    ("case", "Cases"),
    ("quantity", "Quantity"),
    ("free_quantity", "Free Quantity"),
    ("rate", "Purchase Rate (Excl. Tax)"),
    ("tax_inclusive_rate", "Rate Including Tax"),
    ("scheme_discount", "Scheme Discount"),
    ("cash_discount", "Cash Discount"),
    ("discount_percent", "Discount %"),
    ("discount_amount", "Discount Amount"),
    ("other_discount", "Other Discount"),
    ("gross_amount", "Gross Amount"),
    ("taxable", "Taxable Amount"),
    ("gst", "GST %"),
    ("cgst_percent", "CGST %"),
    ("sgst_percent", "SGST %"),
    ("igst_percent", "IGST %"),
    ("cgst_amount", "CGST Amount"),
    ("sgst_amount", "SGST Amount"),
    ("igst_amount", "IGST Amount"),
    ("tax_amount", "Total Tax Amount"),
    ("net", "Line Amount"),
]
VALID_HEADER_ROLES = {role for role, _label in HEADER_ROLES}
TEMPLATE_PARAMETER_PREFIX = "wholesale_shop_pos.ocr_template"


def _normalize_label(value):
    return "".join(
        character for character in (value or "").upper()
        if character == "%" or unicodedata.category(character)[0] in "LMN"
    )

DEFAULT_TEMPLATE_ALIASES = {
    "bill_number": ["Bill No", "Bill Number", "Invoice No", "Invoice Number", "பில் எண்", "ரசீது எண்"],
    "bill_date": ["Bill Date", "Invoice Date", "Dated", "Date", "பில் தேதி", "தேதி", "நாள்"],
    "serial": ["Sl", "Sl No", "S.No", "Sr No", "வரிசை எண்"],
    "description": ["Product Name", "Description", "Description of Goods", "Item", "Particulars", "பொருள்", "பொருள் பெயர்", "விவரம்"],
    "hsn": ["HSN", "HSN No", "HSN/SAC"],
    "barcode": ["Barcode", "EAN"],
    "upc": ["UPC", "Units/Case"],
    "mrp": ["MRP"],
    "case": ["Case", "Cases", "CS"],
    "quantity": ["Qty", "Quantity", "Pcs", "அளவு", "எண்ணிக்கை"],
    "free_quantity": ["Free", "Free Qty"],
    "rate": ["Rate", "Base Rate", "Unit Price", "Price", "விலை"],
    "tax_inclusive_rate": ["Net Rate", "Rate Incl Tax", "Rate Incl. of Tax"],
    "scheme_discount": ["Sch Disc", "Scheme Discount"],
    "cash_discount": ["RS Disc", "Cash Discount", "CD Amt"],
    "discount_percent": ["Disc %", "Discount %"],
    "taxable": ["Taxable", "Taxable Amt", "Taxable Amount"],
    "gst": ["GST", "GST %", "Tax %", "வரி %"],
    "cgst_percent": ["CGST %", "CGST"],
    "sgst_percent": ["SGST %", "SGST", "UTGST %", "UTGST"],
    "igst_percent": ["IGST %"],
    "net": ["Amount", "Net Amount", "Total"],
}

# Physical columns used by stacked, multi-row product tables. These aliases
# remain vendor-editable; the parser does not depend on a vendor name.
DEFAULT_TEMPLATE_ALIASES["serial"].extend(["Item No", "Line No"])
DEFAULT_TEMPLATE_ALIASES["description"].extend(["Item Name", "SKU Description"])
DEFAULT_TEMPLATE_ALIASES.update({
    "uom": ["UOM", "Unit"],
    "secondary_quantity": ["Qty in SUOM", "Secondary Qty", "Weight"],
    "discount_amount": ["Disc Amt", "Disc.Amt", "Discount Amount"],
    "other_discount": ["Other Disc", "Other Discount"],
    "gross_amount": ["Gross Amt", "GrossAmt", "Gross Amount"],
    "cgst_amount": ["CGST Amt", "CGST Amount"],
    "sgst_amount": ["SGST Amt", "SGST Amount"],
    "igst_amount": ["IGST Amt", "IGST Amount"],
    "tax_amount": ["Tot.Tax", "Total Tax", "Tax Amount"],
})


class ShopBillOCRTemplate(models.Model):
    _name = "shop.bill.ocr.template"
    _description = "Vendor Bill OCR Template"
    _order = "partner_id, name"

    name = fields.Char(required=True, default="Default Bill Template")
    template_code = fields.Char(
        required=True,
        default="default",
        help="Short identifier for this vendor's bill layout, for example retail or wholesale.",
    )
    active = fields.Boolean(default=True)
    data_rows_per_item = fields.Integer(
        string="Printed Rows per Product",
        default=1,
        required=True,
        help=(
            "Use 1 for normal tables. Use 2 when every product is printed on "
            "an upper row and a lower detail/tax row. Header aliases on each "
            "printed header row determine how the corresponding value row is read."
        ),
    )
    defaults_initialized = fields.Boolean(default=False, copy=False)
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company, index=True
    )
    partner_id = fields.Many2one(
        "res.partner",
        string="Vendor",
        required=True,
        check_company=True,
        index=True,
        domain="[('shop_is_vendor', '=', True)]",
    )
    alias_ids = fields.One2many(
        "shop.bill.ocr.header.alias", "template_id", string="Printed Header Aliases"
    )
    notes = fields.Text(
        help="Optional notes about this vendor's invoice layout, rotations or photo requirements."
    )
    config_key = fields.Char(compute="_compute_config_key", string="Configuration Key")

    _company_vendor_unique = models.Constraint(
        "UNIQUE(company_id, partner_id, template_code)",
        "The template code must be unique for this vendor and company.",
    )

    @api.constrains("data_rows_per_item")
    def _check_data_rows_per_item(self):
        for template in self:
            if template.data_rows_per_item not in (1, 2, 3):
                raise ValidationError(_("Printed Rows per Product must be 1, 2, or 3."))

    def _compute_config_key(self):
        for template in self:
            template.config_key = (
                "%s.%s" % (TEMPLATE_PARAMETER_PREFIX, template.id)
                if template.id else False
            )

    def _parameter_key(self):
        self.ensure_one()
        return "%s.%s" % (TEMPLATE_PARAMETER_PREFIX, self.id)

    def _alias_payload(self):
        self.ensure_one()
        payload = {}
        for alias in self.alias_ids.filtered("active"):
            payload.setdefault(alias.role, []).append(alias.printed_label.strip())
        return payload

    def _sync_config_parameter(self):
        Parameters = self.env["ir.config_parameter"].sudo()
        for template in self:
            key = template._parameter_key()
            payload = {
                "version": 2,
                "template_id": template.id,
                "template_code": template.template_code,
                "company_id": template.company_id.id,
                "vendor_id": template.partner_id.id,
                "data_rows_per_item": template.data_rows_per_item,
                "aliases": template._alias_payload() if template.active else {},
            }
            Parameters.set_param(key, json.dumps(payload, ensure_ascii=False, sort_keys=True))

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            if not values.get("defaults_initialized"):
                values["alias_ids"] = list(values.get("alias_ids") or [])
                supplied = {
                    (command[2].get("role"), _normalize_label(command[2].get("printed_label")))
                    for command in values["alias_ids"]
                    if isinstance(command, (tuple, list)) and len(command) > 2
                    and command[0] == 0 and isinstance(command[2], dict)
                }
                for role, labels in DEFAULT_TEMPLATE_ALIASES.items():
                    for label in labels:
                        normalized = _normalize_label(label)
                        key = (role, normalized)
                        if not normalized or key in supplied:
                            continue
                        values["alias_ids"].append(
                            (0, 0, {"role": role, "printed_label": label})
                        )
                        supplied.add(key)
                values["defaults_initialized"] = True
        templates = super().create(vals_list)
        templates._sync_config_parameter()
        return templates

    def write(self, vals):
        old_keys = [
            template._parameter_key()
            for template in self
        ]
        result = super().write(vals)
        Parameters = self.env["ir.config_parameter"].sudo()
        current_keys = {
            template._parameter_key()
            for template in self
        }
        for key in set(old_keys) - current_keys:
            Parameters.set_param(key, "{}")
        self._sync_config_parameter()
        return result

    def unlink(self):
        keys = [
            template._parameter_key()
            for template in self
        ]
        result = super().unlink()
        Parameters = self.env["ir.config_parameter"].sudo()
        for key in keys:
            Parameters.set_param(key, "{}")
        return result

    @api.model
    def ensure_for_vendor(self, company, vendor):
        if not company or not vendor:
            return self.browse()
        vendor._mark_as_shop_vendor()
        templates = self.with_context(active_test=False).search(
            [("company_id", "=", company.id), ("partner_id", "=", vendor.id)]
        )
        if templates:
            # Merge newly shipped semantic headings into existing vendor
            # templates as the generic parser gains layout capabilities.
            # Inactive aliases count as intentional configuration and are not
            # recreated; vendor-specific aliases remain untouched.
            for template in templates:
                existing = {
                    (alias.role, alias.normalized_label)
                    for alias in template.with_context(active_test=False).alias_ids
                }
                commands = []
                for role, labels in DEFAULT_TEMPLATE_ALIASES.items():
                    for label in labels:
                        normalized = _normalize_label(label)
                        key = (role, normalized)
                        if normalized and key not in existing:
                            commands.append((0, 0, {"role": role, "printed_label": label}))
                            existing.add(key)
                if commands or not template.defaults_initialized:
                    template.write({
                        "alias_ids": commands,
                        "defaults_initialized": True,
                    })
            return templates.filtered("active") or templates
        return self.create({
            "name": _("%(vendor)s Bill Template", vendor=vendor.display_name),
            "template_code": "default",
            "company_id": company.id,
            "partner_id": vendor.id,
        })

    def _read_template_parameter(self):
        self.ensure_one()
        key = self._parameter_key()
        raw = self.env["ir.config_parameter"].sudo().get_param(key, "{}")
        try:
            payload = json.loads(raw or "{}")
        except (TypeError, ValueError):
            return {}
        if not isinstance(payload, dict):
            payload = {}
        aliases = payload.get("aliases", payload)
        if not isinstance(aliases, dict):
            aliases = {}
        result = {}
        for role, labels in aliases.items():
            if role not in VALID_HEADER_ROLES:
                continue
            if isinstance(labels, str):
                labels = [labels]
            if isinstance(labels, list):
                cleaned = [
                    str(label).strip()
                    for label in labels
                    if label is not None and str(label).strip()
                ]
                if cleaned:
                    result[role] = cleaned
        return {
            "strict": True,
            "template_id": self.id,
            "template_code": self.template_code,
            "vendor_name": self.partner_id.name,
            "vendor_tax_id": self.partner_id.vat,
            "data_rows_per_item": max(
                1, min(int(payload.get("data_rows_per_item") or 1), 3)
            ),
            "aliases": result,
        }

    def get_extraction_config(self):
        self.ensure_one()
        # Keep the parameter synchronized even if an administrator removed it.
        if not self.env["ir.config_parameter"].sudo().get_param(self._parameter_key()):
            self._sync_config_parameter()
        return self._read_template_parameter()


class ShopBillOCRHeaderAlias(models.Model):
    _name = "shop.bill.ocr.header.alias"
    _description = "Vendor Bill OCR Header Alias"
    _order = "role, printed_label"

    active = fields.Boolean(default=True)
    template_id = fields.Many2one(
        "shop.bill.ocr.template", required=True, ondelete="cascade", index=True
    )
    role = fields.Selection(HEADER_ROLES, required=True, index=True)
    printed_label = fields.Char(
        string="Header Printed on Bill",
        required=True,
        help="Exact or OCR-readable heading used by this vendor, for example Item Details or Billed Qty.",
    )
    normalized_label = fields.Char(
        compute="_compute_normalized_label", store=True, precompute=True, index=True
    )

    _template_role_label_unique = models.Constraint(
        "UNIQUE(template_id, role, normalized_label)",
        "This printed header is already configured for this field.",
    )

    @api.depends("printed_label")
    def _compute_normalized_label(self):
        for alias in self:
            alias.normalized_label = "".join(
                character for character in (alias.printed_label or "").upper()
                if character == "%" or unicodedata.category(character)[0] in "LMN"
            )

    @api.constrains("printed_label")
    def _check_printed_label(self):
        for alias in self:
            if not alias.normalized_label:
                raise ValidationError(_("Enter a readable printed header name."))

    @api.model_create_multi
    def create(self, vals_list):
        aliases = super().create(vals_list)
        aliases.template_id._sync_config_parameter()
        return aliases

    def write(self, vals):
        templates = self.template_id
        result = super().write(vals)
        (templates | self.template_id)._sync_config_parameter()
        return result

    def unlink(self):
        templates = self.template_id
        result = super().unlink()
        templates._sync_config_parameter()
        return result
