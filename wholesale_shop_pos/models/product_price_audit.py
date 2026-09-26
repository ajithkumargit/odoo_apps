"""Capture final product prices across nested ORM updates without duplicate rows."""
from contextlib import contextmanager
from contextvars import ContextVar

from odoo import api, fields, models
from odoo.tools.float_utils import float_compare

_changes = ContextVar('shop_product_price_changes', default=None)


def _capture(products, initial=False):
    changes = _changes.get()
    for product in products:
        key = (product.id, product.env.company.id)
        if initial or key not in changes:
            changes[key] = (product, 0.0 if initial else product.standard_price,
                            0.0 if initial else product.shop_variant_sale_price)


@contextmanager
def _track(env, source):
    if _changes.get() is not None:
        yield
        return
    token = _changes.set({})
    try:
        yield
        values = []
        for product, old_cost, old_sale in _changes.get().values():
            if not product.exists():
                continue
            for kind, old, new in [('cost', old_cost, product.standard_price),
                                   ('sale', old_sale, product.shop_variant_sale_price)]:
                if float_compare(old, new, precision_digits=6) == 0:
                    continue
                values.append({
                    'company_id': product.env.company.id,
                    'product_id': product.id,
                    'currency_id': product.env.company.currency_id.id,
                    'price_type': kind,
                    'previous_price': old,
                    'unit_price': new,
                    'changed_at': fields.Datetime.now(),
                    'changed_by_id': env.uid,
                    'source_description': source,
                })
        if values:
            # Users allowed to edit a product must be able to append its audit.
            env['shop.product.price.history'].sudo().create(values)
    finally:
        _changes.reset(token)


class ProductPriceAudit(models.Model):
    _inherit = 'product.product'

    @api.model_create_multi
    def create(self, vals_list):
        with _track(self.env, 'Product creation'):
            products = super().create(vals_list)
            _capture(products, initial=True)
        return products

    def write(self, vals):
        with _track(self.env, 'Product variant update'):
            _capture(self)
            result = super().write(vals)
        return result


class TemplatePriceAudit(models.Model):
    _inherit = 'product.template'

    @api.model_create_multi
    def create(self, vals_list):
        with _track(self.env, 'Product creation'):
            products = super().create(vals_list)
        return products

    def write(self, vals):
        with _track(self.env, 'Product template update'):
            _capture(self.with_context(active_test=False).product_variant_ids)
            result = super().write(vals)
        return result


class AttributePriceAudit(models.Model):
    _inherit = 'product.template.attribute.value'

    def write(self, vals):
        with _track(self.env, 'Variant attribute price update'):
            _capture(self.product_tmpl_id.with_context(active_test=False).product_variant_ids)
            result = super().write(vals)
        return result
