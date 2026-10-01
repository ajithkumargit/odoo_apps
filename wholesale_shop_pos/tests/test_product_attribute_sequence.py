from odoo import Command
from odoo.tests.common import TransactionCase


class TestPosAttributeSequence(TransactionCase):
    def test_pos_receives_draggable_sequence_for_regular_values(self):
        attribute = self.env['product.attribute'].create({'name': 'Flavour'})
        first, second = self.env['product.attribute.value'].create([
            {'name': 'Vanilla', 'attribute_id': attribute.id, 'sequence': 20},
            {'name': 'Chocolate', 'attribute_id': attribute.id, 'sequence': 10},
        ])
        template = self.env['product.template'].create({
            'name': 'Sequence test product',
            'attribute_line_ids': [Command.create({
                'attribute_id': attribute.id,
                'value_ids': [Command.set([first.id, second.id])],
            })],
        })
        values = template.attribute_line_ids.product_template_value_ids
        self.assertEqual(
            {value.name: value.shop_pos_sequence for value in values},
            {'Vanilla': 20, 'Chocolate': 10},
        )
        fields = self.env['product.template.attribute.value']._load_pos_data_fields(False)
        self.assertIn('shop_pos_sequence', fields)
        first.sequence = 5
        self.assertEqual(values.filtered(lambda value: value.name == 'Vanilla').shop_pos_sequence, 5)
