# models/finance.py
import copy
import base64
import io
from datetime import datetime
from openpyxl import load_workbook, Workbook
from copy import copy
import requests

from odoo import api, models, fields
from odoo.orm.environments import defaultdict


# ---------------------------------------------------------------------------
# Helper: normalise raw bank text into a short stable key
# ---------------------------------------------------------------------------

def _normalise(text):
    """Strip whitespace/newlines, uppercase. Used on both sides of matching."""
    return (text or '').replace('\n', '').replace(' ', '').strip().upper()


def _get_match_key(description):
    """
    Convert raw bank description into a short, stable lookup key.

    UPI / IMPS  → last-3 and last-2 slash-separated parts
    NEFT        → last star-separated part (or second-to-last when 5+ parts)
    Other       → cleaned description as-is
    """
    description = _normalise(description)
    upper = description.upper()

    if 'UPI' in upper or 'IMPS' in upper:
        parts = description.split('/')
        if len(parts) >= 3:
            return f"{parts[-3]}/{parts[-2]}"

    if 'NEFT' in upper:
        parts = description.split('*')
        if len(parts) == 4:
            return parts[-1]
        elif len(parts) > 4:
            return parts[-2]

    return description


# ---------------------------------------------------------------------------
# Import Transactions
# ---------------------------------------------------------------------------

class ImportTransactions(models.Model):
    _name = 'import.transactions'

    name = fields.Char()
    file = fields.Binary()
    type_id = fields.Many2one(
        "selection.model",
        domain="[('constant','=','import.transactions.type')]"
    )
    headers = fields.Char()
    python_code = fields.Text()
    transaction_count = fields.Integer()

    def customizeExcel(self, vals):
        result = {'status': 'failure', 'message': 'File Not Loaded', 'binary': ''}
        uploaded_file = vals['uploadedFIle']
        file_content = uploaded_file.read()

        old_wb = load_workbook(io.BytesIO(file_content))
        old_ws = old_wb.active

        new_wb = Workbook()
        new_ws = new_wb.active
        new_ws.title = "Processed Statement"

        for row in old_ws.iter_rows(min_row=1, max_row=2):
            for cell in row:
                new_cell = new_ws[cell.coordinate]
                new_cell.value = cell.value
                if cell.has_style:
                    new_cell.font = copy(cell.font)
                    new_cell.fill = copy(cell.fill)
                    new_cell.border = copy(cell.border)
                    new_cell.alignment = copy(cell.alignment)
                    new_cell.number_format = cell.number_format

        for col, dim in old_ws.column_dimensions.items():
            new_ws.column_dimensions[col].width = dim.width

        output = io.BytesIO()
        new_wb.save(output)
        output.seek(0)

        new_filename = f"processed_statement-{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}.xlsx"
        result['status'] = 'success'
        result['binary'] = base64.b64encode(output.getvalue()).decode()
        result['filename'] = new_filename
        return result


# ---------------------------------------------------------------------------
# Finance Category
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Finance Match Text  (shared keyword pool)
# ---------------------------------------------------------------------------

class FinanceMatchText(models.Model):
    _name = 'finance.match.text'
    _description = 'Transaction Match Keyword'

    name = fields.Char(string='Match Text', required=True)
    subcategory_ids = fields.Many2many(
        'finance.category',
        'finance_category_match_text_rel',
        'match_text_id',
        'category_id',
        string='Subcategories',
    )

    _sql_constraints = [
        ('name_unique', 'UNIQUE(name)', 'This match text already exists.'),
    ]


# ---------------------------------------------------------------------------
# Finance Category  (updated)
# ---------------------------------------------------------------------------

class FinanceCategory(models.Model):
    _name = 'finance.category'

    name = fields.Char()
    active = fields.Boolean(default=True)
    type_id = fields.Many2one(
        "selection.model",
        domain="[('constant','=','finance.category.type')]"
    )
    color = fields.Integer()

    parent_id = fields.Many2one(
        'finance.category',
        string='Parent Category'
    )
    child_ids = fields.One2many(
        'finance.category',
        'parent_id',
        string='Sub Categories'
    )

    # Many2many to the shared keyword pool
    # Only set on subcategories (those with parent_id)
    match_text_ids = fields.Many2many(
        'finance.match.text',
        'finance_category_match_text_rel',
        'category_id',
        'match_text_id',
        string='Match Texts',
        help='Keywords/phrases — if any appear in a transaction name or '
             'description, that transaction is assigned this subcategory.',
    )

    fold = fields.Boolean( store=False)

    def _compute_fold(self):
        Transaction = self.env['finance.transaction']
        for rec in self:
            child_ids = rec.child_ids.ids
            has_transactions = bool(
                Transaction.search_count([
                    '|',
                    ('category_id', '=', rec.id),
                    ('subcategory_id', 'in', child_ids),
                ])
            )
            rec.fold = not has_transactions

    def _get_match_texts_list(self):
        """Return list of normalised keywords for this subcategory."""
        self.ensure_one()
        return [_normalise(mt.name) for mt in self.match_text_ids if mt.name]

    def _add_match_text(self, text):
        """
        Add a keyword to this subcategory's match_text_ids.
        Creates the finance.match.text record if it doesn't exist yet.
        """
        self.ensure_one()
        key = (text or '').strip()
        if not key:
            return

        MatchText = self.env['finance.match.text'].sudo()

        existing = MatchText.search([('name', '=', key)], limit=1)
        if not existing:
            existing = MatchText.create({'name': key})

        # Link to this subcategory if not already linked
        if existing not in self.match_text_ids:
            self.sudo().write({
                'match_text_ids': [(4, existing.id)]
            })
# ---------------------------------------------------------------------------
# Finance Account
# ---------------------------------------------------------------------------

class FinanceAccount(models.Model):
    _name = 'finance.account'
    _rec_name = 'bankname'

    bankname = fields.Char()
    account_number = fields.Char()
    currency_id = fields.Many2one("res.currency")
    active = fields.Boolean(default=True)


# ---------------------------------------------------------------------------
# Selection Model
# ---------------------------------------------------------------------------

class SelectionModel(models.Model):
    _name = 'selection.model'

    name = fields.Char()
    constant = fields.Char()


# ---------------------------------------------------------------------------
# Finance Transaction
# ---------------------------------------------------------------------------

class FinanceTransaction(models.Model):
    _name = 'finance.transaction'
    _rec_name = 'description'

    name = fields.Char()           # normalised / matched description key
    description = fields.Char()    # raw bank description

    status = fields.Many2one(
        "selection.model",
        domain="[('constant','=','finance.transaction.status')]"
    )
    transaction_datetime = fields.Datetime()
    type_id = fields.Many2one(
        "selection.model",
        domain="[('constant','=','finance.transaction.type')]"
    )
    amount = fields.Float()
    account_id = fields.Many2one("finance.account")
    active = fields.Boolean(default=True)
    created_from = fields.Char(default="Manual Entry")

    status_code = fields.Char(related='status.name', store=True)
    type_name = fields.Char(related='type_id.name', store=True)

    category_id = fields.Many2one(
        'finance.category',
        string='Category'
    )
    subcategory_id = fields.Many2one(
        'finance.category',
        string='Sub Category',
        domain="[('parent_id','=',category_id)]",
        group_expand='_group_expand_subcategory'
    )
    running_balance = fields.Float(
        string="Running Balance",
        compute="_compute_running_balance",
        store=True,
    )

    @api.depends(
        "amount",
        "type_id",
        "category_id",
        "transaction_datetime",
    )
    def _compute_running_balance(self):
        for category in self.mapped("category_id"):
            balance = 0
            transactions = self.search(
                [("category_id", "=", category.id)],
                order="transaction_datetime,id",
            )
            for tx in transactions:

                if tx.type_id.name == "income":
                    balance += tx.amount
                else:
                    balance -= tx.amount

                tx.running_balance = balance
    @api.model
    def _group_expand_subcategory(self, subcategories=None, domain=None, order=None):
        return self.env['finance.category'].search([
            ('parent_id', '!=', False),
            ('active', '=', True)
        ])
    # -----------------------------------------------------------------------
    # Onchange helpers
    # -----------------------------------------------------------------------

    @api.onchange('subcategory_id')
    def _onchange_subcategory(self):
        if self.subcategory_id and self.subcategory_id.parent_id:
            self.category_id = self.subcategory_id.parent_id

    @api.onchange('category_id')
    def _onchange_category(self):
        if self.subcategory_id and self.subcategory_id.parent_id != self.category_id:
            self.subcategory_id = False

    # -----------------------------------------------------------------------
    # Write — auto-learn when user manually sets a subcategory
    # -----------------------------------------------------------------------
    def write(self, vals):
        old_subcategories = {rec.id: rec.subcategory_id.id for rec in self}

        result = super().write(vals)

        if 'subcategory_id' in vals:
            for rec in self:
                new_sub = rec.subcategory_id
                if not new_sub:
                    continue
                if old_subcategories[rec.id] == new_sub.id:
                    continue

                learn_text = (rec.name or '').strip()
                if learn_text:
                    new_sub._add_match_text(learn_text)

        return result

    # -----------------------------------------------------------------------
    # Build match cache  →  { NORMALISED_KEYWORD: (subcategory_id, category_id) }
    # -----------------------------------------------------------------------

    @api.model
    def _build_match_cache(self, user_id=None):
        """
        Builds a flat lookup dict from all subcategory match_text_ids.

        Key   : normalised keyword (upper, no spaces)
        Value : (subcategory_id, category_id)
        """
        cache = {}
        subcategories = self.env['finance.category'].sudo().with_user(user_id or self.env.user.id).search([
            ('parent_id', '!=', False),
            ('active', '=', True),
            ('match_text_ids', '!=', False),
        ])
        for sub in subcategories:
            parent_id = sub.parent_id.id
            for keyword in sub._get_match_texts_list():
                if keyword:
                    # Longer (more specific) keyword wins if same key appears twice
                    if keyword not in cache or len(keyword) > len(cache[keyword]):
                        cache[keyword] = (sub.id, parent_id)
        return cache
    # -----------------------------------------------------------------------
    # Match a single transaction against the cache
    # Returns (subcategory_id, category_id) or (False, False)
    # -----------------------------------------------------------------------

    @api.model
    def _match_transaction(self, transaction, cache):
        """
        Check transaction.name and transaction.description against every
        keyword in the cache.  Returns the match for the LONGEST matching
        keyword (most specific wins).
        """
        name_norm = _normalise(transaction.name)
        desc_norm = _normalise(transaction.description)

        best_key = None
        best_match = None

        for keyword, value in cache.items():
            if keyword and (keyword in name_norm or keyword in desc_norm):
                if best_key is None or len(keyword) > len(best_key):
                    best_key = keyword
                    best_match = value

        return best_match or (False, False)

    # -----------------------------------------------------------------------
    # Reallocate: re-match all non-approved transactions
    # -----------------------------------------------------------------------

    def reallocate_category(self, domain=None):
        cache = self._build_match_cache()

        if not cache:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Nothing to do',
                    'message': 'No subcategories have match texts defined.',
                    'type': 'warning',
                    'sticky': False,
                }
            }

        transactions = self.search(
            domain or [('status_code', '!=', 'approved')]
        )

        updated = 0
        for transaction in transactions:
            new_sub_id, new_cat_id = self._match_transaction(transaction, cache)

            if not new_sub_id:
                continue

            changed = (
                transaction.subcategory_id.id != new_sub_id
                or transaction.category_id.id != new_cat_id
            )
            if changed:
                # Call super().write to skip the auto-learn logic on bulk ops
                models.Model.write(transaction, {
                    'subcategory_id': new_sub_id,
                    'category_id': new_cat_id,
                })
                updated += 1

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Done',
                'message': f'{updated} transaction(s) reallocated.',
                'type': 'success',
                'sticky': False,
            }
        }

    # -----------------------------------------------------------------------
    # Bulk approve
    # -----------------------------------------------------------------------

    def action_approve_selected(self):
        approved_status = self.env['selection.model'].sudo().search([
            ('constant', '=', 'finance.transaction.status'),
            ('name', '=', 'approved'),
        ], limit=1)

        self.write({'status': approved_status.id})

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Success',
                'message': f'{len(self)} record(s) approved.',
                'type': 'success',
                'sticky': False,
            }
        }
    
    @api.model
    def get_dashboard_data(self, from_date=False, to_date=False):

        domain = []

        if from_date:
            domain.append(
                ('transaction_datetime', '>=', from_date)
            )

        if to_date:
            domain.append(
                ('transaction_datetime', '<=', to_date)
            )

        transactions = self.search(domain)

        salary = 0
        angelone = 0
        income = 0
        expense = 0

        for rec in transactions:

            if rec.category_id.name == 'Salary':
                salary += rec.amount

            if rec.category_id.name == 'AngelOne Stock':
                angelone += rec.amount

            if rec.type_id.name == 'income':
                income += rec.amount

            if rec.type_id.name == 'expense':
                expense += rec.amount

        return {
            'salary': salary,
            'angelone': angelone,
            'income': income,
            'expense': expense,
        }
    
    @api.model
    def get_chart_data(self, params):

        domain = []

        if params.get("category_ids"):
            domain.append((
                "category_id",
                "in",
                params["category_ids"]
            ))

        if params.get("type_ids"):
            domain.append((
                "type_id",
                "in",
                params["type_ids"]
            ))

        if params.get("account_ids"):
            domain.append((
                "account_id",
                "in",
                params["account_ids"]
            ))

        print("DOMAIN =", domain)
        print("GROUPBY =", [
            params["x_axis"],
            "category_id",
            "type_id",
        ])
        data = self.read_group(
            domain,
            ["amount:sum"],
            [
                params["x_axis"],
                "category_id",
                "type_id",
            ],
            lazy=False,
        )

        return self._format_chartjs(
            data,
            params["x_axis"],
        )
    
    def _format_chartjs(self, data, x_axis):

        labels = []
        seen = set()

        datasets = defaultdict(dict)

        for row in data:

            category = (
                row['category_id'][1]
                if row.get('category_id')
                else 'Unknown'
            )

            type_name = (
                row['type_id'][1]
                if row.get('type_id')
                else 'Unknown'
            )

            dataset = f"{category} ({type_name})"

            # Dynamic X Axis Label
            if x_axis.endswith(':month'):
                label = row.get(x_axis)

            elif x_axis.endswith(':year'):
                label = row.get(x_axis)

            elif x_axis == 'category_id':
                label = row['category_id'][1]

            elif x_axis == 'subcategory_id':
                label = row['subcategory_id'][1]

            elif x_axis == 'account_id':
                label = row['account_id'][1]

            elif x_axis == 'type_id':
                label = row['type_id'][1]

            else:
                label = 'Unknown'
            
            if label not in seen:
                labels.append(label)
                seen.add(label)

            amount = row["amount"]

            # Optional: show expense below zero
            if type_name.lower() == "expense":
                amount = -amount

            datasets[dataset][label] = amount

        result_datasets = []

        for dataset, values in datasets.items():

            result_datasets.append({
            'label': dataset,
                    'data': [
                        values.get(label, 0)
                        for label in labels
                    ]
                })

        return {
            'labels': labels,
            'datasets': result_datasets,
        }
    

class ImportWizard(models.TransientModel):
    _name = "import.wizard"
    _description = "Import File"

    file = fields.Binary(required=True)
    filename = fields.Char(required=True)
    password = fields.Char(string="File Password", required=True)
    user_id = fields.Many2one(
        "res.users",
        string="Import As",
        required=True,
        default=lambda self: self.env.user,
    )

    def action_import(self):
        self.ensure_one()

        files = {
            "file": (
                self.filename,
                base64.b64decode(self.file),
                "application/octet-stream",
            )
        }

        data = {
            "password": self.password,
            "user_id": self.user_id.id,
        }

        response = requests.post(
            "http://localhost:8070/process/statement",
            data=data,
            files=files,
            timeout=300,
        )

        response.raise_for_status()

        return {"type": "ir.actions.act_window_close"}