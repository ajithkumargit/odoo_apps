from odoo import api, fields, models


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    def write(self, vals):
        result = super().write(vals)
        if 'list_price' in vals and not self.env.context.get('shop_variant_price_sync'):
            for product in self.with_context(active_test=False).product_variant_ids.filtered(
                lambda p: p.shop_sale_price_fixed or p.shop_variant_profit_percent > 0
            ):
                product._set_shop_sale_price(product.list_price + product.price_extra, sync_template=False)
        return result


class ProductProduct(models.Model):
    _inherit = 'product.product'

    shop_sale_price_fixed = fields.Boolean(copy=True)
    shop_fixed_sale_price = fields.Float(digits='Product Price', copy=True)

    def _set_shop_sale_price(self, price, sync_template=True):
        self.ensure_one()
        self.with_context(shop_variant_price_sync=True).write({
            'shop_sale_price_fixed': True, 'shop_fixed_sale_price': price,
        })
        if sync_template and self.product_tmpl_id.product_variant_count <= 1:
            self.product_tmpl_id.with_context(shop_variant_price_sync=True).write({
                'list_price': price - self.price_extra,
            })

    def _freeze_legacy_shop_price(self):
        for product in self.filtered(lambda p: p.shop_variant_profit_percent > 0 and not p.shop_sale_price_fixed):
            product._set_shop_sale_price(product.shop_variant_sale_price, sync_template=False)

    def _inverse_shop_variant_sale_price(self):
        for product in self:
            product._set_shop_sale_price(product.shop_variant_sale_price)

    def _set_product_lst_price(self):
        for product in self:
            price = product.lst_price
            if self.env.context.get('uom'):
                price = self.env['uom.uom'].browse(self.env.context['uom'])._compute_price(price, product.uom_id)
            product._set_shop_sale_price(price)

    @api.model_create_multi
    def create(self, vals_list):
        products = super().create(vals_list)
        for product, vals in zip(products, vals_list):
            if 'shop_variant_profit_percent' in vals and not {'list_price', 'lst_price', 'shop_variant_sale_price'}.intersection(vals):
                product._set_shop_sale_price(product.currency_id.round(product.standard_price * (1 + product.shop_variant_profit_percent / 100)))
            elif 'list_price' in vals and product.shop_variant_profit_percent > 0:
                product._set_shop_sale_price(product.list_price + product.price_extra)
        return products

    def write(self, vals):
        if self.env.context.get('shop_variant_price_sync'):
            return super().write(vals)
        changed = self.filtered(lambda p: 'shop_variant_profit_percent' in vals and p.shop_variant_profit_percent != vals['shop_variant_profit_percent'])
        # Snapshot the old amount before a cost import can affect legacy records.
        if 'standard_price' in vals:
            self._freeze_legacy_shop_price()
        result = super().write(vals)
        if not {'list_price', 'lst_price', 'shop_variant_sale_price', 'shop_fixed_sale_price'}.intersection(vals):
            for product in changed:
                product._set_shop_sale_price(product.currency_id.round(product.standard_price * (1 + product.shop_variant_profit_percent / 100)))
        return result
