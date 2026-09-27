import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.fields import Command


class ShopPurchaseImportCreateProductWizard(models.TransientModel):
    _name = "shop.purchase.import.create.product.wizard"
    _description = "Create Product from Purchase Bill"

    line_id = fields.Many2one(
        "shop.purchase.import.line",
        required=True,
        readonly=True,
        ondelete="cascade",
    )
    company_id = fields.Many2one(related="line_id.company_id", readonly=True)
    vendor_id = fields.Many2one(
        "res.partner",
        string="Vendor",
        required=True,
        check_company=True,
        domain="[('shop_is_vendor', '=', True), ('company_id', 'in', [False, company_id])]",
    )
    company_currency_id = fields.Many2one(
        related="company_id.currency_id",
        string="Cost Currency",
        readonly=True,
    )
    creation_mode = fields.Selection(
        [
            ("product", "New Standalone Product"),
            ("new_variant", "New Product Template with Variant"),
            ("existing_variant", "Variant of Existing Product Template"),
        ],
        required=True,
        default="product",
    )
    product_name = fields.Char(required=True)
    product_tmpl_id = fields.Many2one(
        "product.template",
        string="Existing Product Template",
        domain="[('purchase_ok', '=', True), '|', ('company_id', '=', False), ('company_id', '=', company_id)]",
    )
    variant_spec = fields.Text(
        string="Variant Values",
        help=(
            "Enter one Attribute: Value pair per line or separate pairs with semicolons. "
            "Example: Size: 200G; Pack: 10"
        ),
    )
    default_code = fields.Char(string="Internal Reference")
    barcode = fields.Char()
    hsn_code = fields.Char(string="HSN/SAC Code")
    standard_price = fields.Monetary(
        string="Initial Cost",
        currency_field="company_currency_id",
        required=True,
    )
    bill_currency_id = fields.Many2one(related="line_id.currency_id", readonly=True)
    mrp = fields.Monetary(string="MRP", currency_field="bill_currency_id")
    uom_id = fields.Many2one("uom.uom", string="Unit", required=True)
    categ_id = fields.Many2one("product.category", string="Product Category")
    box_quantity = fields.Float(string="Box Quantity", default=1.0, required=True)
    sale_ok = fields.Boolean(string="Can Be Sold", default=True)
    is_storable = fields.Boolean(string="Track Inventory", default=True)

    @api.onchange("creation_mode")
    def _onchange_creation_mode(self):
        if self.creation_mode != "existing_variant":
            self.product_tmpl_id = False

    @api.constrains("box_quantity", "standard_price", "mrp")
    def _check_values(self):
        for wizard in self:
            if wizard.box_quantity <= 0:
                raise ValidationError(_("Box quantity must be greater than zero."))
            if wizard.standard_price < 0:
                raise ValidationError(_("Initial cost cannot be negative."))
            if wizard.mrp < 0:
                raise ValidationError(_("MRP cannot be negative."))

    def _parse_variant_spec(self):
        self.ensure_one()
        parts = [
            part.strip()
            for part in re.split(r"[;\n]+", self.variant_spec or "")
            if part.strip()
        ]
        if not parts:
            raise UserError(
                _("Enter at least one variant value, for example: Size: 200G")
            )
        result = []
        seen_attributes = set()
        for part in parts:
            if ":" not in part:
                raise UserError(
                    _(
                        "Variant value '%(value)s' must use the format Attribute: Value.",
                        value=part,
                    )
                )
            attribute_name, value_name = (value.strip() for value in part.split(":", 1))
            if not attribute_name or not value_name:
                raise UserError(
                    _(
                        "Variant value '%(value)s' must use the format Attribute: Value.",
                        value=part,
                    )
                )
            attribute_key = attribute_name.casefold()
            if attribute_key in seen_attributes:
                raise UserError(
                    _(
                        "Only one value can be selected for the %(attribute)s attribute.",
                        attribute=attribute_name,
                    )
                )
            seen_attributes.add(attribute_key)
            result.append((attribute_name, value_name))
        return result

    def _get_or_create_attribute_values(self, pairs):
        Attribute = self.env["product.attribute"]
        AttributeValue = self.env["product.attribute.value"]
        values = AttributeValue
        for attribute_name, value_name in pairs:
            attribute = Attribute.search(
                [("name", "=ilike", attribute_name)], limit=1
            )
            if not attribute:
                attribute = Attribute.create({
                    "name": attribute_name,
                    "create_variant": "always",
                })
            if attribute.create_variant == "no_variant":
                raise UserError(
                    _(
                        "The %(attribute)s attribute is configured not to create variants.",
                        attribute=attribute.display_name,
                    )
                )
            value = AttributeValue.search([
                ("attribute_id", "=", attribute.id),
                ("name", "=ilike", value_name),
            ], limit=1)
            if not value:
                value = AttributeValue.create({
                    "attribute_id": attribute.id,
                    "name": value_name,
                })
            values |= value
        return values

    def _ensure_template_attribute_values(self, template, attribute_values):
        AttributeLine = self.env["product.template.attribute.line"]
        for value in attribute_values:
            line = template.attribute_line_ids.filtered(
                lambda candidate: candidate.attribute_id == value.attribute_id
            )[:1]
            if line:
                if value not in line.value_ids:
                    line.write({"value_ids": [Command.link(value.id)]})
            else:
                AttributeLine.create({
                    "product_tmpl_id": template.id,
                    "attribute_id": value.attribute_id.id,
                    "value_ids": [Command.set(value.ids)],
                })

        template.invalidate_recordset(["attribute_line_ids", "product_variant_ids"])
        selected_by_attribute = {
            value.attribute_id.id: value for value in attribute_values
        }
        combination = self.env["product.template.attribute.value"]
        missing_attributes = []
        variant_lines = (
            template.valid_product_template_attribute_line_ids
            ._without_no_variant_attributes()
        )
        for line in variant_lines:
            selected_value = selected_by_attribute.get(line.attribute_id.id)
            if selected_value:
                template_value = line.product_template_value_ids.filtered(
                    lambda candidate: (
                        candidate.ptav_active
                        and candidate.product_attribute_value_id == selected_value
                    )
                )[:1]
                combination |= template_value
                continue

            active_values = line.product_template_value_ids.filtered("ptav_active")
            if len(active_values) == 1:
                combination |= active_values
            else:
                missing_attributes.append(line.attribute_id.display_name)

        if missing_attributes:
            raise UserError(
                _(
                    "Select one value for every variant attribute. Missing: %(attributes)s",
                    attributes=", ".join(missing_attributes),
                )
            )
        return combination

    def _new_template_values(self, attribute_values=False):
        self.ensure_one()
        line = self.line_id
        product_name = (self.product_name or "").strip()
        if not product_name:
            raise UserError(_("Enter a product name."))
        values = {
            "name": product_name,
            "type": "consu",
            "is_storable": self.is_storable,
            "sale_ok": self.sale_ok,
            "purchase_ok": True,
            "available_in_pos": True,
            "company_id": self.company_id.id,
            "uom_id": self.uom_id.id,
            "description_purchase": line.raw_description,
            "shop_hsn_code": self.hsn_code or False,
            "supplier_taxes_id": [Command.set(line.tax_ids.ids)],
        }
        if self.categ_id:
            values["categ_id"] = self.categ_id.id
        if attribute_values:
            values["attribute_line_ids"] = [
                Command.create({
                    "attribute_id": attribute.id,
                    "value_ids": [Command.set(
                        attribute_values.filtered(
                            lambda value: value.attribute_id == attribute
                        ).ids
                    )],
                })
                for attribute in attribute_values.attribute_id
            ]
        return values

    def action_create_product(self):
        self.ensure_one()
        line = self.line_id
        if line.product_id:
            raise UserError(_("This bill line is already matched to a product."))
        if not self.vendor_id:
            raise UserError(
                _("Select the vendor before creating and matching a product.")
            )
        if line.import_id.vendor_id != self.vendor_id:
            line.import_id.vendor_id = self.vendor_id
        if line.import_id.state not in ("draft", "review"):
            raise UserError(_("Products can only be created while reviewing the bill import."))

        line._check_product_barcode(barcode=self.barcode)
        ProductTemplate = self.env["product.template"].with_company(self.company_id)

        if self.creation_mode == "product":
            template = ProductTemplate.create(self._new_template_values())
            product = template.product_variant_id
        else:
            pairs = self._parse_variant_spec()
            attribute_values = self._get_or_create_attribute_values(pairs)
            if self.creation_mode == "existing_variant":
                if not self.product_tmpl_id:
                    raise UserError(_("Select the existing product template."))
                template = self.product_tmpl_id
                if template.company_id and template.company_id != self.company_id:
                    raise UserError(_("Select a product template from the bill's company."))
                template.write({"available_in_pos": True, "sale_ok": True})
                combination = self._ensure_template_attribute_values(
                    template, attribute_values
                )
            else:
                template = ProductTemplate.create(
                    self._new_template_values(attribute_values=attribute_values)
                )
                combination = self._ensure_template_attribute_values(
                    template, attribute_values
                )

            product = template._get_variant_for_combination(combination)
            if not product and template.has_dynamic_attributes():
                product = template._create_product_variant(combination)
            if not product:
                template._create_variant_ids()
                product = template._get_variant_for_combination(combination)
            if not product:
                raise UserError(_("Odoo could not create the requested product variant."))

        line._attach_created_product(
            product,
            standard_price=self.standard_price,
            mrp=self.mrp,
            barcode=self.barcode,
            default_code=self.default_code,
            box_quantity=self.box_quantity,
            hsn_code=self.hsn_code,
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Product created"),
                "message": _("Created and matched %(product)s.", product=product.display_name),
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
