from odoo import api, fields, models


class ProductTemplateAttributeValue(models.Model):
    _inherit = 'product.template.attribute.value'

    shop_pos_sequence = fields.Integer(related='product_attribute_value_id.sequence')

    @api.model
    def _load_pos_data_fields(self, config):
        return super()._load_pos_data_fields(config) + ['shop_pos_sequence']
