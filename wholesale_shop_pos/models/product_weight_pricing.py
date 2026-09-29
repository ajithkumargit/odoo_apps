import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


def grams_from_name(name):
    match = re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*(kg|kgs|kilo|kilogram|kilograms|g|gm|gms|gram|grams|கிராம்|கிலோ)\s*', name or '', re.I)
    if not match:
        return 0.0
    amount, unit = match.groups()
    return float(amount) * (1000 if unit.lower().startswith('k') or unit == 'கிலோ' else 1)


class ProductAttributeValue(models.Model):
    _inherit = 'product.attribute.value'

    shop_weight_grams = fields.Float(
        string='Weight (g)', compute='_compute_shop_weight_grams', store=True, readonly=False,
        help='Read automatically from names such as 250g or 0.5kg. For a custom label, enter its weight in grams.',
    )

    @api.depends('name')
    def _compute_shop_weight_grams(self):
        for value in self:
            value.shop_weight_grams = grams_from_name(value.with_context(lang='en_US').name)

    @api.constrains('shop_weight_grams')
    def _check_shop_weight_grams(self):
        if any(value.shop_weight_grams < 0 for value in self):
            raise ValidationError(_('Weight cannot be negative.'))

    def write(self, vals):
        result = super().write(vals)
        if {'name', 'shop_weight_grams'}.intersection(vals):
            self.pav_attribute_line_ids.product_tmpl_id._sync_shop_weight_prices()
        return result


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    shop_price_by_weight = fields.Boolean(string='Price by Weight',
        help='Sales Price is the price for 1 kg. Each weight variant is sold as one pack, with proportional cost and sales price.')
    shop_weight_attribute_id = fields.Many2one('product.attribute', string='Weight Attribute',
        help='Select the variant attribute containing weights such as 100g, 250g, 500g and 1kg.')
    shop_cost_per_kg = fields.Float(string='Cost / kg', digits='Product Price', company_dependent=True)
    shop_sale_price_per_kg = fields.Float(string='Sales Price / kg', related='list_price', readonly=False, digits='Product Price')

    def _guess_shop_weight_attribute(self):
        self.ensure_one()
        candidates = self.attribute_line_ids.filtered(
            lambda line: line.value_ids and all(value.shop_weight_grams > 0 for value in line.value_ids)
            and line.attribute_id.create_variant != 'no_variant'
        )
        return candidates.attribute_id if len(candidates) == 1 else self.env['product.attribute']

    @api.onchange('shop_price_by_weight', 'attribute_line_ids')
    def _onchange_shop_weight_attribute(self):
        for template in self:
            if template.shop_price_by_weight and not template.shop_weight_attribute_id:
                template.shop_weight_attribute_id = template._guess_shop_weight_attribute()

    def _check_shop_weight_configuration(self):
        self.ensure_one()
        lines = self.attribute_line_ids.filtered(lambda line: line.attribute_id == self.shop_weight_attribute_id)
        if not self.shop_weight_attribute_id or not lines:
            raise ValidationError(_('Add a Weight attribute with values such as 100g, 250g and 1kg, then select it as the Weight Attribute.'))
        if self.shop_weight_attribute_id.create_variant == 'no_variant':
            raise ValidationError(_('The Weight Attribute must create product variants (Instantly or Dynamically).'))
        if any(value.shop_weight_grams <= 0 for value in lines.value_ids):
            raise ValidationError(_('Every weight value needs a positive Weight (g). Use a name such as 250g, or enter grams on the attribute value.'))
        if any(value.price_extra for value in lines.product_template_value_ids.filtered('ptav_active')):
            raise ValidationError(_('Set the extra prices on the Weight Attribute to zero. Price by Weight calculates the complete pack price.'))
        if self.uom_id != self.env.ref('uom.product_uom_unit'):
            raise ValidationError(_('Weight variants represent individual packs. Set their unit of measure to Units before enabling Price by Weight.'))
        if self.shop_cost_per_kg < 0 or self.list_price < 0:
            raise ValidationError(_('Cost / kg and Sales Price / kg cannot be negative.'))

    def _sync_shop_weight_prices(self):
        if self.env.context.get('shop_weight_sync') or self.env.context.get('shop_weight_defer'):
            return
        for template in self.filtered('shop_price_by_weight'):
            if not template.shop_weight_attribute_id:
                candidate = template._guess_shop_weight_attribute()
                if candidate:
                    template.with_context(shop_weight_defer=True).shop_weight_attribute_id = candidate
            template._check_shop_weight_configuration()
            template.product_variant_ids._apply_shop_weight_prices()

    def _apply_shop_profit_price(self):
        normal = self.filtered(lambda template: not template.shop_price_by_weight)
        super(ProductTemplate, normal)._apply_shop_profit_price()
        for template in self - normal:
            template.with_context(shop_profit_price_sync=True).list_price = template.currency_id.round(
                template.shop_cost_per_kg * (1 + template.shop_profit_percent / 100)
            )

    @api.onchange('shop_profit_percent')
    def _onchange_shop_profit_percent(self):
        super()._onchange_shop_profit_percent()
        for template in self.filtered('shop_price_by_weight'):
            template.list_price = template.shop_cost_per_kg * (1 + template.shop_profit_percent / 100)

    @api.model_create_multi
    def create(self, vals_list):
        templates = super(ProductTemplate, self.with_context(shop_weight_defer=True)).create(vals_list).with_env(self.env)
        templates._sync_shop_weight_prices()
        return templates

    def write(self, vals):
        if self.env.context.get('shop_weight_sync') or self.env.context.get('shop_weight_defer'):
            return super().write(vals)
        if 'standard_price' in vals and any(
            vals.get('shop_price_by_weight', template.shop_price_by_weight) for template in self
        ):
            raise ValidationError(_('Use Cost / kg to change the cost of a product with Price by Weight enabled.'))
        result = super(ProductTemplate, self.with_context(shop_weight_defer=True)).write(vals)
        if {'shop_price_by_weight', 'shop_weight_attribute_id', 'shop_cost_per_kg', 'list_price',
            'shop_sale_price_per_kg', 'shop_profit_percent', 'attribute_line_ids', 'uom_id'}.intersection(vals):
            self._sync_shop_weight_prices()
        return result


class ProductProduct(models.Model):
    _inherit = 'product.product'

    shop_variant_weight_grams = fields.Float(string='Pack Weight (g)', compute='_compute_variant_weight', store=True)

    @api.depends('product_tmpl_id.shop_weight_attribute_id',
                 'product_template_attribute_value_ids.product_attribute_value_id.shop_weight_grams')
    def _compute_variant_weight(self):
        for product in self:
            values = product.product_template_attribute_value_ids.filtered(
                lambda value: value.attribute_id == product.shop_weight_attribute_id
            )
            product.shop_variant_weight_grams = values.product_attribute_value_id.shop_weight_grams if len(values) == 1 else 0

    def _apply_shop_weight_prices(self):
        for product in self.filtered(lambda p: p.shop_price_by_weight and p.active):
            if product.shop_variant_weight_grams <= 0:
                raise ValidationError(_('Select exactly one positive weight for each variant.'))
            ratio = product.shop_variant_weight_grams / 1000
            product.with_context(shop_weight_sync=True, shop_variant_price_sync=True).write({
                'standard_price': product.shop_cost_per_kg * ratio,
                'shop_fixed_sale_price': product.currency_id.round(product.list_price * ratio + product.price_extra),
                'shop_sale_price_fixed': True,
            })

    @api.model_create_multi
    def create(self, vals_list):
        products = super().create(vals_list)
        if not self.env.context.get('shop_weight_defer'):
            products.filtered('shop_price_by_weight')._apply_shop_weight_prices()
        return products

    def write(self, vals):
        if self.env.context.get('shop_weight_sync') or self.env.context.get('shop_weight_defer') or self.env.context.get('shop_variant_price_sync'):
            return super().write(vals)
        weighted = self.filtered('shop_price_by_weight')
        if weighted and {'lst_price', 'shop_variant_sale_price', 'shop_variant_profit_percent'}.intersection(vals):
            raise ValidationError(_('Price by Weight is enabled. Change Sales Price / kg or Profit % on the main product, or disable Price by Weight to edit individual prices.'))
        if weighted and 'standard_price' in vals:
            # Supplier bills provide cost per matched pack: convert to the kg basis.
            costs = {}
            for product in weighted:
                if product.shop_variant_weight_grams <= 0:
                    raise ValidationError(_('Set the variant weight before updating its cost.'))
                cost = vals['standard_price'] * 1000 / product.shop_variant_weight_grams
                template = product.product_tmpl_id
                if template in costs and abs(costs[template] - cost) > 0.000001:
                    raise ValidationError(_('Update Cost / kg on the main product instead of setting the same pack cost on different weights.'))
                costs[template] = cost
            other_values = {key: value for key, value in vals.items() if key != 'standard_price'}
            if other_values:
                super(ProductProduct, weighted).write(other_values)
            if self - weighted:
                super(ProductProduct, self - weighted).write(vals)
            for template, cost in costs.items():
                template.shop_cost_per_kg = cost
            return True
        result = super().write(vals)
        if {'product_template_attribute_value_ids', 'active'}.intersection(vals):
            weighted._apply_shop_weight_prices()
        return result


class ProductTemplateAttributeLine(models.Model):
    _inherit = 'product.template.attribute.line'

    def write(self, vals):
        result = super().write(vals)
        if {'value_ids', 'attribute_id', 'active'}.intersection(vals):
            self.product_tmpl_id._sync_shop_weight_prices()
        return result

    def unlink(self):
        templates = self.product_tmpl_id
        result = super().unlink()
        templates.exists()._sync_shop_weight_prices()
        return result


class ProductTemplateAttributeValue(models.Model):
    _inherit = 'product.template.attribute.value'

    def write(self, vals):
        result = super().write(vals)
        if 'price_extra' in vals:
            self.product_tmpl_id._sync_shop_weight_prices()
        return result
