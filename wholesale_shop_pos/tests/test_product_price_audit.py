from odoo.tests.common import TransactionCase


class TestProductPriceAudit(TransactionCase):
    def setUp(self):
        super().setUp()
        self.product = self.env['product.product'].create({
            'name': 'Price audit test', 'standard_price': 10, 'list_price': 20,
        }).with_env(self.env)
        self.History = self.env['shop.product.price.history']
        self.cutoff = self.History.search([], order='id desc', limit=1).id

    def changes(self):
        return self.History.search([('id', '>', self.cutoff), ('product_id', '=', self.product.id)])

    def test_cost_and_automatic_sale_recorded_once(self):
        self.product.shop_variant_profit_percent = 50
        self.cutoff = self.History.search([], order='id desc', limit=1).id
        self.product.standard_price = 12
        rows = self.changes()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows.filtered(lambda r: r.price_type == 'cost').previous_price, 10)
        self.assertEqual(rows.filtered(lambda r: r.price_type == 'cost').unit_price, 12)
        self.assertEqual(rows.filtered(lambda r: r.price_type == 'sale').unit_price, 18)

    def test_template_sale_and_cost_inverse(self):
        self.product.product_tmpl_id.write({'standard_price': 15, 'list_price': 25})
        rows = self.changes()
        self.assertEqual(len(rows), 2)
        self.assertEqual(set(rows.mapped('price_type')), {'cost', 'sale'})
        self.assertEqual(set(rows.mapped('changed_by_id').ids), {self.env.uid})

    def test_no_change_does_not_add_history(self):
        self.product.write({'standard_price': 10, 'list_price': 20, 'name': 'Renamed'})
        self.assertFalse(self.changes())

    def test_template_profit_does_not_duplicate_sale(self):
        self.product.product_tmpl_id.shop_profit_percent = 20
        self.cutoff = self.History.search([], order='id desc', limit=1).id
        self.product.standard_price = 20
        rows = self.changes()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows.filtered(lambda r: r.price_type == 'sale').unit_price, 24)

    def test_variant_extra_price(self):
        attribute = self.env['product.attribute'].create({'name': 'Audit size'})
        value = self.env['product.attribute.value'].create([{'name': 'Large', 'attribute_id': attribute.id}, {'name': 'Small', 'attribute_id': attribute.id}])
        self.product.product_tmpl_id.attribute_line_ids = [(0, 0, {
            'attribute_id': attribute.id, 'value_ids': [(6, 0, value.ids)],
        })]
        self.product = self.product.product_tmpl_id.product_variant_ids[:1]
        self.cutoff = self.History.search([], order='id desc', limit=1).id
        self.product.product_template_attribute_value_ids.price_extra = 5
        rows = self.changes()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows.price_type, 'sale')
        self.assertEqual(rows.unit_price, 25)

    def test_batch_update_records_each_variant(self):
        other = self.product.copy({'name': 'Second audit product'})
        self.cutoff = self.History.search([], order='id desc', limit=1).id
        (self.product | other).write({'standard_price': 30})
        rows = self.History.search([('id', '>', self.cutoff)])
        self.assertEqual(set(rows.mapped('product_id').ids), {self.product.id, other.id})
        self.assertEqual(len(rows), 2)

    def test_failed_transaction_leaves_no_history(self):
        try:
            with self.env.cr.savepoint():
                self.product.standard_price = 99
                raise ValueError('Rollback the price edit')
        except ValueError:
            pass
        self.assertFalse(self.changes())
        self.product.standard_price = 11
        self.assertEqual(len(self.changes()), 1)

    def test_company_cost_is_tracked_in_its_company(self):
        other = self.env['res.company'].create({'name': 'Audit company', 'currency_id': self.env.company.currency_id.id})
        product = self.product.with_company(other)
        product.standard_price = 40
        rows = self.changes().filtered(lambda r: r.price_type == 'cost')
        self.assertEqual(rows.company_id, other)
        self.assertEqual(rows.unit_price, 40)
        self.assertEqual(self.product.standard_price, 10)

    def test_creation_records_initial_cost_and_sale(self):
        product = self.env['product.product'].create({'name': 'Initial price audit', 'standard_price': 7, 'list_price': 9})
        rows = self.History.search([('product_id', '=', product.id)])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows.filtered(lambda r: r.price_type == 'cost').unit_price, 7)
        self.assertEqual(rows.filtered(lambda r: r.price_type == 'sale').unit_price, 9)
        self.assertEqual(set(rows.mapped('previous_price')), {0.0})
