from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    shop_mrp = fields.Monetary(
        string='MRP', currency_field='currency_id',
        compute='_compute_shop_mrp', inverse='_inverse_shop_mrp', store=True,
        help='Maximum retail price per sale unit. Set MRP on each variant for products with multiple variants.',
    )

    @api.depends('product_variant_ids.shop_mrp', 'product_variant_ids.active')
    def _compute_shop_mrp(self):
        self._compute_template_field_from_variant_field('shop_mrp', default=0.0)

    def _inverse_shop_mrp(self):
        for product in self:
            if len(product.product_variant_ids) > 1:
                # Odoo may inverse stored template fields while regenerating
                # variants after an attribute edit. A multi-variant template
                # has no single MRP to write back to those variants.
                continue
            product._set_product_variant_field('shop_mrp')

    def _get_related_fields_variant_template(self):
        return super()._get_related_fields_variant_template() + ['shop_mrp']


class ProductProduct(models.Model):
    _inherit = 'product.product'

    shop_mrp = fields.Monetary(
        string='MRP', currency_field='currency_id', default=0.0,
        help='Maximum retail price per sale unit, including taxes. This does not change the sales price or cost.',
    )

    @api.constrains('shop_mrp')
    def _check_shop_mrp(self):
        if any(product.shop_mrp < 0 for product in self):
            raise ValidationError(_('MRP cannot be negative.'))
