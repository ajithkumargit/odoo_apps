from odoo.tests.common import TransactionCase


class TestFinanceClassification(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.savings = cls.env.ref('expense_tracker.classification_savings')
        cls.mandatory = cls.env.ref('expense_tracker.classification_mandatory')
        cls.category = cls.env['finance.category'].create({
            'name': 'Classification test', 'classification_id': cls.savings.id,
        })
        cls.transaction = cls.env['finance.transaction'].create({
            'description': 'Classification regression', 'category_id': cls.category.id,
            'amount': 10,
        })

    def test_category_default_updates_existing_transactions(self):
        self.assertEqual(self.transaction.classification_id, self.savings)
        self.category.classification_id = self.mandatory
        self.assertEqual(self.transaction.classification_id, self.mandatory)

    def test_override_survives_category_changes_and_can_reset(self):
        self.transaction.classification_id = self.mandatory
        self.assertEqual(self.transaction.classification_override_id, self.mandatory)
        self.category.classification_id = False
        self.assertEqual(self.transaction.classification_id, self.mandatory)
        self.transaction.action_use_category_classification()
        self.assertFalse(self.transaction.classification_id)

    def test_subcategory_default_takes_precedence(self):
        subcategory = self.env['finance.category'].create({
            'name': 'Classification child', 'parent_id': self.category.id,
            'classification_id': self.mandatory.id,
        })
        self.transaction.subcategory_id = subcategory
        self.assertEqual(self.transaction.classification_id, self.mandatory)
        subcategory.classification_id = False
        self.assertEqual(self.transaction.classification_id, self.savings)

    def test_custom_classification_and_grouping(self):
        custom = self.env['finance.classification'].create({'name': 'Classification custom test'})
        self.transaction.classification_id = custom
        groups = self.env['finance.transaction']._read_group(
            [('id', '=', self.transaction.id)], ['classification_id'], ['amount:sum'],
        )
        self.assertEqual(groups, [(custom, 10)])

    def test_clearing_override_restores_default(self):
        self.transaction.classification_id = self.mandatory
        self.transaction.classification_id = False
        self.assertFalse(self.transaction.classification_override_id)
        self.assertEqual(self.transaction.classification_id, self.savings)
