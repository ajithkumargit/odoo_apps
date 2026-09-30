from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class ProductCategory(models.Model):
    _inherit = "product.category"

    shop_pos_category_id = fields.Many2one(
        "pos.category",
        string="Matching POS Category",
        copy=False,
        index=True,
    )

    def _ensure_shop_pos_category(self):
        PosCategory = self.env["pos.category"]
        for category in self:
            if category.parent_id:
                category.parent_id._ensure_shop_pos_category()
            if category.shop_pos_category_id:
                continue
            parent_pos = category.parent_id.shop_pos_category_id if category.parent_id else False
            pos_category = PosCategory.search(
                [
                    ("name", "=", category.name),
                    ("parent_id", "=", parent_pos.id if parent_pos else False),
                ],
                limit=1,
            )
            if not pos_category:
                pos_category = PosCategory.with_context(shop_category_sync=True).create(
                    {
                        "name": category.name,
                        "parent_id": parent_pos.id if parent_pos else False,
                    }
                )
            category.with_context(shop_category_sync=True).write(
                {"shop_pos_category_id": pos_category.id}
            )
            if pos_category.shop_product_category_id != category:
                pos_category.with_context(shop_category_sync=True).write(
                    {"shop_product_category_id": category.id}
                )
        return self.mapped("shop_pos_category_id")

    @api.model_create_multi
    def create(self, vals_list):
        categories = super().create(vals_list)
        if not self.env.context.get("shop_category_sync"):
            categories._ensure_shop_pos_category()
        return categories

    def write(self, vals):
        result = super().write(vals)
        if (
            not self.env.context.get("shop_category_sync")
            and {"name", "parent_id"}.intersection(vals)
        ):
            for category in self:
                if category.parent_id:
                    category.parent_id._ensure_shop_pos_category()
                category._ensure_shop_pos_category()
                parent_pos = category.parent_id.shop_pos_category_id
                category.shop_pos_category_id.with_context(shop_category_sync=True).write(
                    {
                        "name": category.name,
                        "parent_id": parent_pos.id if parent_pos else False,
                    }
                )
        return result


class PosCategory(models.Model):
    _inherit = "pos.category"

    shop_product_category_id = fields.Many2one(
        "product.category",
        string="Matching Product Category",
        copy=False,
        index=True,
    )

    def _ensure_shop_product_category(self):
        ProductCategory = self.env["product.category"]
        for category in self:
            if category.parent_id:
                category.parent_id._ensure_shop_product_category()
            if category.shop_product_category_id:
                continue
            parent_product = (
                category.parent_id.shop_product_category_id
                if category.parent_id
                else False
            )
            product_category = ProductCategory.search(
                [
                    ("name", "=", category.name),
                    ("parent_id", "=", parent_product.id if parent_product else False),
                ],
                limit=1,
            )
            if not product_category:
                product_category = ProductCategory.with_context(
                    shop_category_sync=True
                ).create(
                    {
                        "name": category.name,
                        "parent_id": parent_product.id if parent_product else False,
                    }
                )
            category.with_context(shop_category_sync=True).write(
                {"shop_product_category_id": product_category.id}
            )
            if product_category.shop_pos_category_id != category:
                product_category.with_context(shop_category_sync=True).write(
                    {"shop_pos_category_id": category.id}
                )
        return self.mapped("shop_product_category_id")

    @api.model_create_multi
    def create(self, vals_list):
        categories = super().create(vals_list)
        if not self.env.context.get("shop_category_sync"):
            categories._ensure_shop_product_category()
        return categories

    def write(self, vals):
        result = super().write(vals)
        if (
            not self.env.context.get("shop_category_sync")
            and {"name", "parent_id"}.intersection(vals)
        ):
            for category in self:
                if category.parent_id:
                    category.parent_id._ensure_shop_product_category()
                category._ensure_shop_product_category()
                parent_product = category.parent_id.shop_product_category_id
                category.shop_product_category_id.with_context(
                    shop_category_sync=True
                ).write(
                    {
                        "name": category.name,
                        "parent_id": parent_product.id if parent_product else False,
                    }
                )
        return result


class ProductTemplate(models.Model):
    _inherit = "product.template"

    shop_hsn_code = fields.Char(
        string="HSN/SAC Code",
        index=True,
        help="HSN/SAC code captured from supplier bills.",
    )
    shop_profit_percent = fields.Float(
        string="Profit %",
        digits=(16, 6),
        default=0.0,
        help=(
            "Changing this percentage sets Sales Price to Cost plus "
            "this percentage. For example, a cost of 100 and profit of 20% "
            "sets the sales price to 120."
        ),
    )
    shop_price_check = fields.Monetary(
        string="Price to Check",
        currency_field="currency_id",
        help="Enter a proposed sales price to see its profit percentage without changing Sales Price.",
    )
    shop_price_check_profit_percent = fields.Float(
        string="Template Profit % at Checked Price",
        digits=(16, 6),
        compute="_compute_shop_price_check_profit_percent",
        help="Profit percentage of Price to Check compared with Cost.",
    )

    @api.depends("shop_price_check", "standard_price")
    def _compute_shop_price_check_profit_percent(self):
        for product in self:
            cost = product.standard_price
            product.shop_price_check_profit_percent = (
                (product.shop_price_check - cost) / cost * 100.0
                if cost and product.shop_price_check else 0.0
            )

    def action_apply_shop_checked_profit(self):
        self.ensure_one()
        if self.standard_price <= 0:
            raise UserError(_("Set a positive Cost before applying the checked profit."))
        if self.shop_price_check <= self.standard_price:
            raise UserError(_("Price to Check must be greater than Cost to apply a profit."))
        self.shop_profit_percent = self.shop_price_check_profit_percent
        return True

    @api.constrains("shop_profit_percent")
    def _check_shop_profit_percent(self):
        for product in self:
            if product.shop_profit_percent < 0:
                raise ValidationError(_("Profit percentage cannot be negative."))

    def _apply_shop_profit_price(self):
        """Synchronize the template sales price from its company cost."""
        for product in self.filtered(
            lambda item: item.product_variant_count <= 1
        ):
            cost = product.with_company(self.env.company).standard_price
            sales_price = self.env.company.currency_id.round(
                cost * (1.0 + product.shop_profit_percent / 100.0)
            )
            if product.list_price != sales_price:
                product.with_context(shop_profit_price_sync=True).write({
                    "list_price": sales_price,
                })

    @api.onchange("shop_profit_percent")
    def _onchange_shop_profit_percent(self):
        for product in self:
            if product.product_variant_count <= 1:
                product.list_price = product.standard_price * (
                    1.0 + product.shop_profit_percent / 100.0
                )

    def _sync_shop_product_pos_category(self, source="product"):
        for product in self:
            if source == "pos" and product.pos_categ_ids:
                pos_category = product.pos_categ_ids[0]
                pos_category._ensure_shop_product_category()
                values = {
                    "categ_id": pos_category.shop_product_category_id.id,
                    "available_in_pos": True,
                }
            else:
                product.categ_id._ensure_shop_pos_category()
                values = {
                    "pos_categ_ids": [
                        (6, 0, product.categ_id.shop_pos_category_id.ids)
                    ],
                    "available_in_pos": True,
                }
            product.with_context(shop_product_pos_sync=True).write(values)

    @api.model_create_multi
    def create(self, vals_list):
        products = super().create(vals_list)
        if not self.env.context.get("shop_product_pos_sync"):
            for product, values in zip(products, vals_list):
                source = "pos" if values.get("pos_categ_ids") else "product"
                product._sync_shop_product_pos_category(source=source)
        for product, values in zip(products, vals_list):
            if "shop_profit_percent" in values and "list_price" not in values:
                product._apply_shop_profit_price()
        return products

    def write(self, vals):
        changed_profit = self.filtered(lambda p: "shop_profit_percent" in vals and p.shop_profit_percent != vals["shop_profit_percent"])
        result = super().write(vals)
        if not self.env.context.get("shop_product_pos_sync"):
            if "categ_id" in vals:
                self._sync_shop_product_pos_category(source="product")
            elif "pos_categ_ids" in vals:
                self._sync_shop_product_pos_category(source="pos")
        if (
            not self.env.context.get("shop_profit_price_sync")
            and "list_price" not in vals and changed_profit
        ):
            changed_profit._apply_shop_profit_price()
        return result


class ProductProduct(models.Model):
    _inherit = "product.product"

    shop_box_barcode = fields.Char(
        string="Box Barcode",
        index=True,
        help="Optional barcode used when scanning a full box/carton in the POS.",
    )
    shop_box_qty = fields.Float(
        string="Box Qty",
        default=1.0,
        help="How many base units are contained in one scanned box/carton.",
    )
    shop_low_stock_qty = fields.Float(
        string="Low Stock Alert Qty",
        default=0.0,
        help="Convenience threshold for the custom mobile/shop screens.",
    )
    shop_variant_profit_percent = fields.Float(
        string="Variant Profit %",
        digits=(16, 6),
        default=0.0,
        help=(
            "Changing this percentage calculates the variant Sales Price once from its cost. "
            "Direct sales-price edits are preserved until the percentage changes again."
        ),
    )
    shop_variant_sale_price = fields.Monetary(
        string="Variant Sales Price",
        currency_field="currency_id",
        compute="_compute_shop_variant_sale_price",
        inverse="_inverse_shop_variant_sale_price",
        help="Editable variant price. Direct cost edits preserve manual prices; reviewed supplier bills use MRP when Profit % is zero, or recalculate from a positive Profit %.",
    )
    shop_variant_price_check = fields.Monetary(
        string="Variant Price to Check",
        currency_field="currency_id",
        help=(
            "Enter a proposed price for this variant to inspect its profit "
            "percentage without changing the POS sales price."
        ),
    )
    shop_variant_price_check_profit_percent = fields.Float(
        string="Variant Profit % at Checked Price",
        digits=(16, 6),
        compute="_compute_shop_variant_price_check_profit_percent",
    )

    @api.depends("shop_variant_price_check", "standard_price")
    def _compute_shop_variant_price_check_profit_percent(self):
        for product in self:
            cost = product.standard_price
            product.shop_variant_price_check_profit_percent = (
                (product.shop_variant_price_check - cost) / cost * 100.0
                if cost and product.shop_variant_price_check else 0.0
            )

    def action_apply_shop_variant_checked_profit(self):
        self.ensure_one()
        if self.standard_price <= 0:
            raise UserError(_("Set a positive variant Cost before applying the checked profit."))
        if self.shop_variant_price_check <= self.standard_price:
            raise UserError(_("Variant Price to Check must be greater than Cost to apply a profit."))
        self.shop_variant_profit_percent = (
            self.shop_variant_price_check_profit_percent
        )
        return True

    @api.constrains("shop_variant_profit_percent")
    def _check_shop_variant_profit_percent(self):
        for product in self:
            if product.shop_variant_profit_percent < 0:
                raise ValidationError(_("Variant profit percentage cannot be negative."))

    @api.depends("standard_price", "shop_variant_profit_percent", "list_price", "price_extra", "shop_fixed_sale_price", "shop_sale_price_fixed")
    @api.depends_context("company")
    def _compute_shop_variant_sale_price(self):
        for product in self:
            if product.shop_sale_price_fixed:
                product.shop_variant_sale_price = product.shop_fixed_sale_price
            elif product.shop_variant_profit_percent > 0:
                product.shop_variant_sale_price = product.currency_id.round(
                    product.standard_price
                    * (1.0 + product.shop_variant_profit_percent / 100.0)
                )
            else:
                product.shop_variant_sale_price = product.list_price + product.price_extra

    @api.depends(
        "list_price", "price_extra", "standard_price", "shop_variant_profit_percent", "shop_fixed_sale_price", "shop_sale_price_fixed"
    )
    @api.depends_context("uom")
    def _compute_product_lst_price(self):
        super()._compute_product_lst_price()
        for product in self.filtered(lambda item: item.shop_sale_price_fixed or item.shop_variant_profit_percent > 0):
            price = product.shop_variant_sale_price
            if self.env.context.get("uom"):
                target_uom = self.env["uom.uom"].browse(self.env.context["uom"])
                price = product.uom_id._compute_price(price, target_uom)
            product.lst_price = price

    def _price_compute(self, price_type, uom=None, currency=None, company=None, date=False):
        prices = super()._price_compute(
            price_type, uom=uom, currency=currency, company=company, date=date
        )
        if price_type != "list_price":
            return prices
        company = company or self.env.company
        date = date or fields.Date.context_today(self)
        for product in self.with_company(company).filtered(
            lambda item: item.shop_sale_price_fixed or item.shop_variant_profit_percent > 0
        ):
            price = product.shop_variant_sale_price
            if uom:
                price = product.uom_id._compute_price(price, uom)
            if currency:
                price = product.currency_id._convert(
                    price, currency, company, date
                )
            prices[product.id] = price
        return prices
