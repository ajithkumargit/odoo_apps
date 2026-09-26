from odoo import models, api, fields
import math

class ProductProduct(models.Model):
    _inherit = 'product.product'

    def write(self, vals):
        res = super(ProductProduct, self).write(vals)
        return res
    
    def create(self, vals):
        res = super(ProductProduct, self).create(vals)
        for product in res:
            self.pricelist_rule_ids.create({'pricelist_id':2,'currency_id':self.env.user.company_id.currency_id.id,'product_tmpl_id':res.product_tmpl_id.id,'product_id':product.id,'fixed_price':res.product_tmpl_id.list_price,"compute_price":"fixed","min_quantity":1})
        return res
    
class ProductPriceListItem(models.Model):
    _inherit = 'product.pricelist.item'

    def write(self, vals):
        res = super(ProductPriceListItem, self).write(vals)
        return res
    
    def create(self, vals):
        res = super(ProductPriceListItem, self).create(vals)
        return res
class ProductTemplate(models.Model):
    _inherit = 'product.template'

    def write(self, vals):
        res = super(ProductTemplate, self).write(vals)
        return res

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def action_confirm(self):
        res = super().action_confirm()
        return res

class ProductPricelistItem(models.Model):
    _inherit = 'product.pricelist.item'

    def _compute_price(self, product, quantity, uom, date, currency=None, **kwargs):
        price = super()._compute_price(product, quantity, uom, date, currency=currency, **kwargs)

        decimal = price - int(price)

        if decimal >= 0.10:
            price = math.ceil(price)
        else:
            price = math.floor(price)

        return price