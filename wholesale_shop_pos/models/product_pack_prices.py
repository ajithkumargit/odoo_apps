from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


PACK_FIELDS = ('shop_box_price', 'shop_box_qty', 'shop_single_pack_price', 'shop_single_pack_qty')


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    shop_box_price = fields.Monetary(string='Box Price', currency_field='currency_id',
        compute='_compute_pack_prices', inverse='_inverse_box_price', store=True)
    shop_box_qty = fields.Float(string='Box Qty', compute='_compute_pack_prices',
        inverse='_inverse_box_qty', store=True,
        help='Number of base units in one box/carton; also used by box barcode scanning.')
    shop_single_pack_price = fields.Monetary(string='Single Pack Price', currency_field='currency_id',
        compute='_compute_pack_prices', inverse='_inverse_single_pack_price', store=True)
    shop_single_pack_qty = fields.Float(string='Single Pack Qty', compute='_compute_pack_prices',
        inverse='_inverse_single_pack_qty', store=True,
        help='Number of base units in one single pack.')

    @api.depends('product_variant_ids.shop_box_price', 'product_variant_ids.shop_box_qty',
                 'product_variant_ids.shop_single_pack_price', 'product_variant_ids.shop_single_pack_qty',
                 'product_variant_ids.active')
    def _compute_pack_prices(self):
        for name in PACK_FIELDS:
            self._compute_template_field_from_variant_field(name, default=0.0)

    def _inverse_pack_field(self, name):
        for template in self:
            if len(template.product_variant_ids) > 1:
                raise ValidationError(_('Set box and single-pack values on each product variant.'))
        self._set_product_variant_field(name)

    def _inverse_box_price(self):
        self._inverse_pack_field('shop_box_price')

    def _inverse_box_qty(self):
        self._inverse_pack_field('shop_box_qty')

    def _inverse_single_pack_price(self):
        self._inverse_pack_field('shop_single_pack_price')

    def _inverse_single_pack_qty(self):
        self._inverse_pack_field('shop_single_pack_qty')

    @api.model_create_multi
    def create(self, vals_list):
        templates = super().create(vals_list)
        # Variants are created after the template's initial inverse methods.
        # Preserve explicit zero values as well as nonzero prices/quantities.
        for template, values in zip(templates, vals_list):
            pack_values = {name: values[name] for name in PACK_FIELDS if name in values}
            if pack_values:
                if len(template.product_variant_ids) > 1:
                    raise ValidationError(_('Set box and single-pack values on each product variant.'))
                template.product_variant_ids.write(pack_values)
        return templates


class ProductProduct(models.Model):
    _inherit = 'product.product'

    shop_box_price = fields.Monetary(string='Box Price', currency_field='currency_id', default=0.0,
        help='Manually recorded price for one box. Does not automatically change Sales Price or Cost.')
    shop_single_pack_price = fields.Monetary(string='Single Pack Price', currency_field='currency_id', default=0.0,
        help='Manually recorded price for one pack. Does not automatically change Sales Price or Cost.')
    shop_single_pack_qty = fields.Float(string='Single Pack Qty', default=1.0,
        help='Number of base units in one single pack.')

    @api.constrains(*PACK_FIELDS)
    def _check_pack_values(self):
        if any(product[name] < 0 for product in self for name in PACK_FIELDS):
            raise ValidationError(_('Box and single-pack prices and quantities cannot be negative.'))
