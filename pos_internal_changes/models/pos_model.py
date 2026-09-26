from odoo import api, fields, models


class PosCategory(models.Model):
    _inherit = "pos.category"

    product_category_id = fields.Many2one(
        "product.category",
        string="Linked Product Category",
        readonly=True,
        copy=False,
    )

    @api.model_create_multi
    def create(self, vals_list):
        categories = super().create(vals_list)

        ProductCategory = self.env["product.category"]

        for category in categories:

            parent = False
            if category.parent_id and category.parent_id.product_category_id:
                parent = category.parent_id.product_category_id.id

            product_category = ProductCategory.create({
                "name": category.name,
                "parent_id": parent,
            })

            category.product_category_id = product_category.id

        return categories

    def write(self, vals):
        res = super().write(vals)

        for category in self:

            if not category.product_category_id:
                continue

            values = {}

            if "name" in vals:
                values["name"] = category.name

            if "parent_id" in vals:
                values["parent_id"] = (
                    category.parent_id.product_category_id.id
                    if category.parent_id and category.parent_id.product_category_id
                    else False
                )

            if values:
                category.product_category_id.write(values)

        return res

    def unlink(self):
        product_categories = self.mapped("product_category_id")
        res = super().unlink()
        product_categories.unlink()
        return res
    
class ProductTemplate(models.Model):
    _inherit = "product.template"

    @api.model_create_multi
    def create(self, vals_list):
        products = super().create(vals_list)

        products._update_product_category()

        return products

    def write(self, vals):
        res = super().write(vals)

        if "pos_categ_ids" in vals:
            self._update_product_category()

        return res

    def _update_product_category(self):
        ProductCategory = self.env["product.category"]

        for product in self:

            if not product.pos_categ_ids:
                continue

            pos_category = product.pos_categ_ids[0]

            category = pos_category.product_category_id

            if not category:
                category = ProductCategory.search(
                    [("name", "=", pos_category.name)],
                    limit=1,
                )

            if category:
                product.categ_id = category.id