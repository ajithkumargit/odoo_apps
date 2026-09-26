from odoo import api, fields, models


class FinanceClassification(models.Model):
    _name = 'finance.classification'
    _description = 'Finance Classification'
    _order = 'sequence, name, id'

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    _name_unique = models.Constraint('UNIQUE(name)', 'This classification already exists.')


class FinanceCategory(models.Model):
    _inherit = 'finance.category'

    classification_id = fields.Many2one(
        'finance.classification', string='Classification', ondelete='restrict',
        help='Default classification for transactions in this category, such as Savings or Mandatory.',
    )


class FinanceTransaction(models.Model):
    _inherit = 'finance.transaction'

    classification_override_id = fields.Many2one(
        'finance.classification', copy=True, ondelete='restrict',
        string='Classification Override',
    )
    classification_id = fields.Many2one(
        'finance.classification', compute='_compute_classification',
        inverse='_inverse_classification', store=True, readonly=False,
        ondelete='restrict', string='Classification',
        help='Uses the subcategory or category default. Choose another classification to override it for this transaction. Clear the field to use the default again.',
    )

    @api.depends('classification_override_id', 'category_id.classification_id',
                 'subcategory_id.classification_id')
    def _compute_classification(self):
        for transaction in self:
            transaction.classification_id = (
                transaction.classification_override_id
                or transaction.subcategory_id.classification_id
                or transaction.category_id.classification_id
            )

    def _inverse_classification(self):
        for transaction in self:
            transaction.classification_override_id = transaction.classification_id
        # The effective field is protected while its inverse runs. Explicitly
        # restore the inherited value when the user clears an override.
        self._compute_classification()

    def action_use_category_classification(self):
        self.write({'classification_override_id': False})
        return True
