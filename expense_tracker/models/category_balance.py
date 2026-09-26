from odoo import api, fields, models


class FinanceCategoryBalance(models.Model):
    _inherit = 'finance.transaction'

    balance_currency_id = fields.Many2one(
        'res.currency', compute='_compute_balance_currency', store=True,
        string='Currency',
    )
    income_amount = fields.Monetary(
        string='Received (+)', currency_field='balance_currency_id',
        compute='_compute_balance_amounts', store=True, aggregator='sum',
    )
    expense_amount = fields.Monetary(
        string='Paid (-)', currency_field='balance_currency_id',
        compute='_compute_balance_amounts', store=True, aggregator='sum',
    )
    signed_amount = fields.Monetary(
        string='Net Balance', currency_field='balance_currency_id',
        compute='_compute_balance_amounts', store=True, aggregator='sum',
        help='Income is positive and expense is negative. Group totals show income minus expense for the filtered transactions.',
    )
    balance_type_known = fields.Boolean(compute='_compute_balance_amounts', store=True)

    @api.depends('account_id.currency_id')
    def _compute_balance_currency(self):
        for transaction in self:
            transaction.balance_currency_id = transaction.account_id.currency_id or self.env.company.currency_id

    @api.depends('amount', 'type_id.name')
    def _compute_balance_amounts(self):
        for transaction in self:
            kind = (transaction.type_id.name or '').strip().lower()
            transaction.income_amount = transaction.amount if kind == 'income' else 0.0
            transaction.expense_amount = -transaction.amount if kind == 'expense' else 0.0
            transaction.signed_amount = transaction.income_amount + transaction.expense_amount
            transaction.balance_type_known = kind in ('income', 'expense')
