from odoo.tests.common import TransactionCase


class TestCategoryBalance(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.category = cls.env['finance.category'].create({'name': 'Balance test category'})
        cls.income, cls.expense, cls.unknown = cls.env['selection.model'].create([
            {'name': name, 'constant': 'finance.transaction.type'}
            for name in ('income', 'expense', 'unknown')
        ])
        cls.transactions = cls.env['finance.transaction']

    def transaction(self, amount, kind, when='2026-09-01 12:00:00', **extra):
        return self.transactions.create({
            'description': 'Balance regression', 'category_id': self.category.id,
            'amount': amount, 'type_id': kind.id, 'transaction_datetime': when, **extra,
        })

    def test_native_group_totals_and_date_range(self):
        self.transaction(100, self.income, '2026-08-31 18:30:00')
        self.transaction(140, self.expense, '2026-09-02 18:29:59')
        self.transaction(1000, self.income, '2026-08-31 18:29:59')
        self.transaction(1000, self.income, '2026-09-02 18:30:00')
        rows = self.transactions._read_group([
            ('category_id', '=', self.category.id),
            ('transaction_datetime', '>=', '2026-08-31 18:30:00'),
            ('transaction_datetime', '<', '2026-09-02 18:30:00'),
        ], ['category_id'], ['income_amount:sum', 'expense_amount:sum', 'signed_amount:sum'])
        self.assertEqual(rows[0][1:], (100, -140, -40))

    def test_form_edits_recompute_amount_and_type(self):
        transaction = self.transaction(75, self.income)
        self.assertEqual(transaction.signed_amount, 75)
        transaction.write({'type_id': self.expense.id, 'amount': 90})
        self.assertEqual(transaction.income_amount, 0)
        self.assertEqual(transaction.expense_amount, -90)
        self.assertEqual(transaction.signed_amount, -90)
        self.expense.name = 'income'
        self.assertEqual(transaction.signed_amount, 90)

    def test_unknown_type_is_not_treated_as_expense(self):
        transaction = self.transaction(500, self.unknown)
        self.assertFalse(transaction.balance_type_known)
        self.assertEqual(transaction.signed_amount, 0)

    def test_currencies_can_be_grouped_separately(self):
        currencies = self.env['res.currency'].with_context(active_test=False).search([], limit=2)
        accounts = self.env['finance.account'].create([
            {'bankname': 'Balance test', 'currency_id': currency.id} for currency in currencies
        ])
        for account in accounts:
            self.transaction(50, self.income, account_id=account.id)
        rows = self.transactions._read_group([('category_id', '=', self.category.id)],
                                             ['balance_currency_id'], ['signed_amount:sum'])
        self.assertEqual(len(rows), 2)
        self.assertEqual([row[1] for row in rows], [50, 50])

    def test_category_balance_has_native_views(self):
        action = self.env.ref('expense_tracker.finance_category_balance_window_action')
        self.assertEqual(action.type, 'ir.actions.act_window')
        self.assertTrue({'list', 'calendar', 'form'}.issubset(action.view_mode.split(',')))
        for view_id, view_type in action.views:
            if view_type not in ('list', 'calendar', 'form'):
                continue
            view = self.transactions.get_view(view_id, view_type)
            self.assertIn('signed_amount', view['arch'])
        menu = self.env.ref('expense_tracker.finance_category_balance_menu')
        self.assertEqual(menu.action, action)
