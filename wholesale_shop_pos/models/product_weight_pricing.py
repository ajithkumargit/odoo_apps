import re
import math

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


WEIGHT_OPTIONS_PARAM = 'wholesale_shop_pos.weight_options'
DEFAULT_WEIGHT_OPTIONS = '100g,200g,250g,500g,750g,1kg'


def configured_weight_names(value):
    names = list(dict.fromkeys(part.strip() for part in value.split(',') if part.strip()))
    if not names or any(not math.isfinite(grams_from_name(name)) or grams_from_name(name) <= 0 for name in names):
        raise ValidationError(_('Enter comma-separated positive weights with units, for example: 100g,200g,500g,750g,1kg.'))
    return names


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    shop_weight_options = fields.Char(
        string='Default Weight Options', config_parameter=WEIGHT_OPTIONS_PARAM,
        default=DEFAULT_WEIGHT_OPTIONS,
    )

    @api.constrains('shop_weight_options')
    def _check_weight_options(self):
        for settings in self:
            configured_weight_names(settings.shop_weight_options or DEFAULT_WEIGHT_OPTIONS)


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
        help='Prices are based on 1 kg. With kg as the unit, POS selects a fractional kg quantity. With Units, variants have proportional pack prices and costs.')
    shop_weight_attribute_id = fields.Many2one('product.attribute', string='Weight Attribute',
        help='Select the variant attribute containing weights such as 100g, 250g, 500g and 1kg.')
    shop_cost_per_kg = fields.Float(string='Cost / kg', digits='Product Price', company_dependent=True)
    shop_sale_price_per_kg = fields.Float(string='Sales Price / kg', related='list_price', readonly=False, digits='Product Price')

    @api.depends_context('company')
    @api.depends('product_variant_ids.standard_price', 'shop_price_by_weight', 'shop_cost_per_kg')
    def _compute_standard_price(self):
        super()._compute_standard_price()
        for template in self.filtered('shop_price_by_weight'):
            template.standard_price = template.shop_cost_per_kg

    def _latest_shop_cost_bill_line(self, product=False):
        self.ensure_one()
        # Include the original archived variant: it still owns historical bills.
        domain = [
            ('product_id.product_tmpl_id', '=', self.id),
            ('company_id', '=', self.env.company.id),
            ('import_id.state', '=', 'po_created'),
            ('import_id.purchase_order_id.state', '!=', 'cancel'),
            ('item_unit_cost_incl_tax', '>', 0),
        ]
        if product:
            domain.append(('product_id', '=', product.id))
        return self.env['shop.purchase.import.line'].search(domain, order='id desc').sorted(
            key=lambda line: (str(line.import_id.bill_date or ''), line.id), reverse=True)[:1]

    def action_update_cost_from_bill(self):
        self.ensure_one()
        line = self._latest_shop_cost_bill_line()
        if not line:
            raise ValidationError(_('No positive Per-Item Cost was found in a purchase-order-created bill for this product and company.'))
        if self.uom_id == self.env.ref('uom.product_uom_kgm'):
            cost = line._initial_cost_in_company_currency()
            if self.shop_price_by_weight:
                self.shop_cost_per_kg = cost
            else:
                self.product_variant_ids.write({'standard_price': cost})
        else:
            line._sync_product_cost(line.product_id)
        return True

    def action_load_shop_weight_options(self):
        self.ensure_one()
        names = configured_weight_names(self.env['ir.config_parameter'].sudo().get_param(
            WEIGHT_OPTIONS_PARAM, DEFAULT_WEIGHT_OPTIONS,
        ) or DEFAULT_WEIGHT_OPTIONS)
        attribute = self.shop_weight_attribute_id or self._guess_shop_weight_attribute()
        if not attribute:
            raise ValidationError(_('Select a Weight Attribute first, then load the configured weights.'))
        if attribute.create_variant == 'no_variant':
            raise ValidationError(_('The Weight Attribute must create product variants.'))
        values = self.env['product.attribute.value']
        for name in names:
            value = values.with_context(lang='en_US').search([
                ('attribute_id', '=', attribute.id), ('name', '=', name),
            ], limit=1)
            if not value:
                value = values.search([
                    ('attribute_id', '=', attribute.id),
                    ('shop_weight_grams', '=', grams_from_name(name)),
                ], limit=1)
            if not value:
                value = values.create({'attribute_id': attribute.id, 'name': name})
            values |= value
        line = self.attribute_line_ids.filtered(lambda item: item.attribute_id == attribute)
        commands = [(1, line.id, {'value_ids': [(4, value.id) for value in values]})] if line else [
            (0, 0, {'attribute_id': attribute.id, 'value_ids': [(6, 0, values.ids)]})
        ]
        self.write({'shop_weight_attribute_id': attribute.id, 'attribute_line_ids': commands})
        return True

    def _guess_shop_weight_attribute(self):
        self.ensure_one()
        candidates = self.attribute_line_ids.filtered(
            lambda line: line.value_ids and all(value.shop_weight_grams > 0 for value in line.value_ids)
            and line.attribute_id.create_variant != 'no_variant'
        )
        return candidates.attribute_id if len(candidates) == 1 else self.env['product.attribute']

    def _prepare_variant_values(self, combination):
        values = super()._prepare_variant_values(combination)
        # Odoo replaces the original variant when multiple values are added.
        # Carry its cost in the unchanged UoM; do not interpret kg as packs.
        # Enabled weight pricing will apply its explicit kg basis afterwards.
        original = self.product_variant_ids
        if (not self.shop_price_by_weight and len(original) == 1
                and not original.product_template_attribute_value_ids
                and any(value.product_attribute_value_id.shop_weight_grams > 0 for value in combination)):
            values['standard_price'] = original.standard_price
        return values

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
        if self.uom_id not in (self.env.ref('uom.product_uom_unit') | self.env.ref('uom.product_uom_kgm')):
            raise ValidationError(_('Use kg for loose-weight sales or Units for separate packs.'))
        if self.shop_cost_per_kg <= 0 or not math.isfinite(self.shop_cost_per_kg):
            raise ValidationError(_('Enter a positive Cost / kg before enabling or updating Price by Weight. Existing pack costs will not be replaced with a blank or zero kg cost.'))
        if self.list_price < 0:
            raise ValidationError(_('Sales Price / kg cannot be negative.'))

    def _sync_shop_weight_prices(self):
        if (self.env.context.get('shop_weight_sync') or self.env.context.get('shop_weight_defer')
                or self.env.context.get('shop_weight_atomic')):
            return
        for template in self.filtered('shop_price_by_weight'):
            if not template.shop_weight_attribute_id:
                candidate = template._guess_shop_weight_attribute()
                if candidate:
                    template.with_context(shop_weight_defer=True).shop_weight_attribute_id = candidate
            if not template.shop_cost_per_kg and template.uom_id == self.env.ref('uom.product_uom_kgm'):
                line = template._latest_shop_cost_bill_line()
                if line:
                    template.with_context(shop_weight_defer=True).shop_cost_per_kg = line._initial_cost_in_company_currency()
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
        weighted = self.filtered(lambda template: vals.get('shop_price_by_weight', template.shop_price_by_weight))
        if 'standard_price' in vals and weighted:
            updates = dict(vals)
            updates.setdefault('shop_cost_per_kg', updates.pop('standard_price'))
            weighted.write(updates)
            if self - weighted:
                (self - weighted).write(vals)
            return True
        result = super(ProductTemplate, self.with_context(shop_weight_defer=True)).write(vals)
        if {'shop_price_by_weight', 'shop_weight_attribute_id', 'shop_cost_per_kg', 'list_price',
            'shop_sale_price_per_kg', 'shop_profit_percent', 'attribute_line_ids', 'uom_id'}.intersection(vals):
            self._sync_shop_weight_prices()
        return result


class ProductProduct(models.Model):
    _inherit = 'product.product'

    shop_loose_weight = fields.Boolean(compute='_compute_shop_loose_weight')
    shop_weight_sale_amount = fields.Monetary(
        string='Sales Amount for Selected Weight', compute='_compute_shop_weight_amounts',
        inverse='_inverse_shop_weight_sale_amount',
        currency_field='currency_id', help='Amount for this weight at the current unit price, before tax calculation.')
    shop_weight_cost_amount = fields.Monetary(
        string='Cost for Selected Weight', compute='_compute_shop_weight_amounts',
        inverse='_inverse_shop_weight_cost_amount', currency_field='cost_currency_id')
    shop_weight_profit_percent = fields.Float(
        string='Profit %', related='product_tmpl_id.shop_profit_percent', readonly=False, digits=(16, 6))

    def _inverse_shop_weight_cost_amount(self):
        for product in self:
            if product.shop_variant_weight_grams <= 0:
                raise ValidationError(_('Select a positive weight before changing its cost.'))
            product.product_tmpl_id.shop_cost_per_kg = product.shop_weight_cost_amount * 1000 / product.shop_variant_weight_grams

    def action_update_cost_from_bill(self):
        self.ensure_one()
        if self.uom_id == self.env.ref('uom.product_uom_kgm'):
            return self.product_tmpl_id.action_update_cost_from_bill()
        line = self.product_tmpl_id._latest_shop_cost_bill_line(product=self)
        if not line:
            raise ValidationError(_('No positive reviewed bill cost was found for this variant and company.'))
        line._sync_product_cost(self)
        return True

    def _inverse_shop_weight_sale_amount(self):
        for product in self:
            if not product.shop_price_by_weight or product.shop_variant_weight_grams <= 0:
                raise ValidationError(_('Select a positive weight before changing its sales amount.'))
            amount = product.shop_weight_sale_amount
            ratio = product.shop_variant_weight_grams / 1000
            extra = product.price_extra * (ratio if product.shop_loose_weight else 1)
            product.product_tmpl_id.shop_sale_price_per_kg = (amount - extra) / ratio

    @api.depends('shop_variant_price_check', 'standard_price', 'shop_weight_cost_amount', 'shop_price_by_weight')
    def _compute_shop_variant_price_check_profit_percent(self):
        super()._compute_shop_variant_price_check_profit_percent()
        for product in self.filtered('shop_price_by_weight'):
            cost = product.shop_weight_cost_amount
            product.shop_variant_price_check_profit_percent = (
                (product.shop_variant_price_check - cost) / cost * 100
                if cost and product.shop_variant_price_check else 0)

    def action_apply_shop_variant_checked_profit(self):
        self.ensure_one()
        if not self.shop_price_by_weight:
            return super().action_apply_shop_variant_checked_profit()
        if self.shop_weight_cost_amount <= 0 or self.shop_variant_price_check <= self.shop_weight_cost_amount:
            raise ValidationError(_('Enter a checked price above the cost for the selected weight.'))
        # Apply the exact entered amount, without a percentage-rounding round trip.
        self.product_tmpl_id.write({
            'shop_profit_percent': self.shop_variant_price_check_profit_percent,
            'list_price': (self.shop_variant_price_check / (self.shop_variant_weight_grams / 1000)
                           - (self.price_extra if self.shop_loose_weight else self.price_extra / (self.shop_variant_weight_grams / 1000))),
        })
        return True

    @api.depends('shop_loose_weight', 'shop_price_by_weight', 'shop_variant_weight_grams',
                 'shop_variant_sale_price', 'standard_price')
    @api.depends_context('company')
    def _compute_shop_weight_amounts(self):
        for product in self:
            ratio = product.shop_variant_weight_grams / 1000 if product.shop_loose_weight else 1
            product.shop_weight_sale_amount = product.shop_variant_sale_price * ratio if product.shop_price_by_weight else 0
            product.shop_weight_cost_amount = product.standard_price * ratio if product.shop_price_by_weight else 0

    @api.depends('shop_price_by_weight', 'uom_id')
    def _compute_shop_loose_weight(self):
        kg = self.env.ref('uom.product_uom_kgm')
        for product in self:
            product.shop_loose_weight = product.shop_price_by_weight and product.uom_id == kg

    @api.model
    def _load_pos_data_fields(self, config):
        return super()._load_pos_data_fields(config) + ['shop_loose_weight', 'shop_variant_weight_grams']

    def action_load_shop_weight_options(self):
        self.ensure_one()
        return self.product_tmpl_id.action_load_shop_weight_options()

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
        if self.env.context.get('shop_weight_atomic'):
            return
        for product in self.filtered(lambda p: p.shop_price_by_weight and p.active):
            # Also protect new/dynamic variants and direct calls, which can bypass
            # the template synchronization entry point.
            if product.shop_cost_per_kg <= 0 or not math.isfinite(product.shop_cost_per_kg):
                raise ValidationError(_('Enter a positive Cost / kg before calculating weight variant costs.'))
            if product.shop_variant_weight_grams <= 0:
                raise ValidationError(_('Select exactly one positive weight for each variant.'))
            ratio = 1 if product.shop_loose_weight else product.shop_variant_weight_grams / 1000
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
        weight_inputs = {
            'shop_price_by_weight', 'shop_weight_attribute_id', 'shop_cost_per_kg',
            'shop_sale_price_per_kg', 'list_price', 'standard_price', 'lst_price',
            'shop_variant_sale_price', 'shop_variant_profit_percent', 'shop_profit_percent',
            'shop_weight_sale_amount', 'shop_weight_cost_amount', 'shop_weight_profit_percent',
            'product_template_attribute_value_ids', 'uom_id', 'active',
        }
        if not self.env.context.get('shop_weight_atomic') and weight_inputs.intersection(vals):
            # Delegated template fields and computed-field inverses are written
            # in separate steps. Validate only after the entire form is saved.
            updates = dict(vals)
            if 'shop_cost_per_kg' in updates:
                updates.pop('standard_price', None)
                updates.pop('shop_weight_cost_amount', None)
            result = self.with_context(shop_weight_atomic=True).write(updates)
            self.product_tmpl_id._sync_shop_weight_prices()
            return result
        weighted = self.filtered('shop_price_by_weight')
        price_keys = {'lst_price', 'shop_variant_sale_price', 'shop_variant_profit_percent'}
        if weighted and price_keys.intersection(vals):
            # Direct edits update the shared basis, rather than being rejected or
            # overwritten by the next cost import. Explicit sale prices win.
            remaining = {key: value for key, value in vals.items() if key not in price_keys}
            if remaining:
                weighted.write(remaining)
            for product in weighted:
                updates = {}
                if ('shop_variant_profit_percent' in vals and
                        vals['shop_variant_profit_percent'] != product.shop_profit_percent):
                    updates['shop_profit_percent'] = vals['shop_variant_profit_percent']
                price = vals.get('lst_price', vals.get('shop_variant_sale_price'))
                if price is not None:
                    if 'lst_price' in vals and self.env.context.get('uom'):
                        price = self.env['uom.uom'].browse(self.env.context['uom'])._compute_price(price, product.uom_id)
                    ratio = 1 if product.shop_loose_weight else product.shop_variant_weight_grams / 1000
                    if ratio <= 0:
                        raise ValidationError(_('Select a positive variant weight first.'))
                    updates['list_price'] = (price - product.price_extra) / ratio
                if updates:
                    product.product_tmpl_id.write(updates)
            if self - weighted:
                (self - weighted).write(vals)
            return True
        if weighted and 'standard_price' in vals:
            # Supplier bills provide cost per matched pack: convert to the kg basis.
            costs = {}
            for product in weighted:
                if product.shop_variant_weight_grams <= 0:
                    raise ValidationError(_('Set the variant weight before updating its cost.'))
                cost = vals['standard_price'] if product.shop_loose_weight else vals['standard_price'] * 1000 / product.shop_variant_weight_grams
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
