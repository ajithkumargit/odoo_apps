# controllers/finance_controller.py
import base64
import json
import tempfile

from odoo import http
from odoo.http import request
from odoo.exceptions import ValidationError

from openpyxl import load_workbook
from io import BytesIO
from datetime import date, datetime

import pdfplumber
import msoffcrypto
from pypdf import PdfReader


class FinanceController(http.Controller):

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _json_response(self, data, status=200):
        return request.make_response(
            json.dumps(data),
            headers=[('Content-Type', 'application/json')],
            status=status,
        )

    def _get_selection(self, constant, name):
        return request.env['selection.model'].sudo().search([
            ('constant', '=', constant),
            ('name', '=', name),
        ], limit=1)

    # -----------------------------------------------------------------------
    # PDF processing
    # -----------------------------------------------------------------------

    def _process_pdf(self, uploaded_file, password, user_id):
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
            tmp.write(uploaded_file.read())
            tmp.flush()
            tmp_path = tmp.name

        try:
            reader = PdfReader(tmp_path)

            if reader.is_encrypted:
                result = reader.decrypt(password or '')
                if result == 0:
                    return self._json_response({'error': 'Invalid PDF password'})

            vals_list = []
            with pdfplumber.open(tmp_path, password=password or '') as pdf:
                for page in pdf.pages:
                    rows = page.extract_tables()
                    if not rows:continue
                    vals_list.extend(rows[0][1:])
                self.process_data(vals_list, user_id, decrypted=None,from_import='PDF Import')

            return self._json_response({'success': True})

        except Exception as e:
            return self._json_response({'error': str(e)})

    # -----------------------------------------------------------------------
    # Excel processing
    # -----------------------------------------------------------------------

    def _process_excel(self, uploaded_file, password, user_id):

        # --- Decrypt ---
        encrypted = BytesIO(uploaded_file.read())
        decrypted = BytesIO()

        try:
            office_file = msoffcrypto.OfficeFile(encrypted)
            if office_file.is_encrypted():
                office_file.load_key(password=password or '')
                office_file.decrypt(decrypted)
                decrypted.seek(0)

            else:
                decrypted = encrypted
        except Exception:
            return self._json_response({'error': 'Invalid Excel password'})

        

        try:
            workbook = load_workbook(decrypted, data_only=True)
        except Exception as e:
            return self._json_response({'error': f'Could not open Excel file: {e}'})

        sheet = workbook.active

        FinanceTransaction = request.env['finance.transaction'].sudo()
        start_parsing = False
        vals_list = []
        for row in sheet.iter_rows(values_only=True):

            # Detect the statement header row
            if row[0] and isinstance(row[0], str) and row[0].startswith('Statement'):
                start_parsing = True
                skip_rows = 1   # skip header + one blank row after it
                continue

            if not start_parsing:
                continue

            if skip_rows > 0:
                skip_rows -= 1
                continue

            # Stop on fully empty row
            if not any(row):
                continue

            # --- Date ---
            raw_date = row[0]
            if not raw_date:
                continue
            
            vals_list.append(row)
            
        decrypted.seek(0)
        # --- Bulk create ---
        self.process_data(vals_list, user_id, decrypted,from_import='Excel Import')

        return self._json_response({
            'success': True,
            'created': len(vals_list),
        })

    # -----------------------------------------------------------------------
    # Main route
    # -----------------------------------------------------------------------

    @http.route(
        '/process/statement',
        type='http',
        auth='public',
        methods=['POST'],
        csrf=False,
    )
    def process_statement(self, **post):
        uploaded_file = post.get('file')
        password      = post.get('password', '')
        user_id       = int(post.get('user_id', request.env.user.id))

        if not uploaded_file:
            return self._json_response({'error': 'No file uploaded'})

        filename = (uploaded_file.filename or '').lower()

        if filename.endswith(('.xlsx', '.xls')):
            return self._process_excel(uploaded_file, password, user_id)

        if filename.endswith('.pdf'):
            return self._process_pdf(uploaded_file, password, user_id)

        return self._json_response({
            'error': 'Only PDF and Excel files are supported.'
        })
    
    def _parse_date(self, value):

        if isinstance(value, datetime):
            return value

        if isinstance(value, date):
            return datetime.combine(value, datetime.min.time())

        if isinstance(value, str):

            value = value.strip()

            for fmt in (
                "%d/%m/%Y",
                "%d %b %Y",
                "%d-%m-%Y",
                "%Y-%m-%d",
            ):
                try:
                    return datetime.strptime(value, fmt)
                except ValueError:
                    pass

        return None

    def process_data(self, vals_list, user_id, decrypted=None,from_import='Excel Import'):
        expense_type = self._get_selection('finance.transaction.type', 'expense')
        income_type  = self._get_selection('finance.transaction.type', 'income')
        inreview_status = self._get_selection('finance.transaction.status', 'inreview')
        import_type = self._get_selection('import.transactions.type', from_import)

        # --- Build match cache from subcategory match_texts ---
        FinanceTransaction = request.env['finance.transaction'].sudo()
        match_cache = FinanceTransaction._build_match_cache(user_id)
        result_list = []
        for row in vals_list:
            date_value = self._parse_date(row[0])
            if not date_value:
                continue

            # --- Amount & type ---
            raw_description = row[1] or ''


            if row[3] and row[3] != '-':
                amount  = row[3]
                type_id = expense_type.id
            elif row[4]:
                amount  = row[4]
                type_id = income_type.id
            else:
                continue   # no amount — skip

            # --- Normalise description to match key ---
            from odoo.addons.expense_tracker.models.models import _get_match_key
            match_key = _get_match_key(raw_description)

            # --- Match against subcategory cache ---
            # Build a lightweight stub so we can reuse _match_transaction
            class _Stub:
                def __init__(self, name, description):
                    self.name = name
                    self.description = description

            stub = _Stub(name=match_key, description=raw_description)
            sub_id, cat_id = FinanceTransaction._match_transaction(stub, match_cache)

            # --- Duplicate check (same amount + same raw description) ---
            duplicate = FinanceTransaction.search_count([
                ('amount', '=', amount),
                ('description', '=', raw_description),
            ])
            if duplicate:
                continue

            result_list.append({
                'transaction_datetime': date_value,
                'name':                 match_key,
                'description':          raw_description,
                'status':               inreview_status.id,
                'amount':               amount,
                'type_id':              type_id,
                'subcategory_id':       sub_id or False,
                'category_id':          cat_id or False,
                'created_from':         from_import,
                'create_uid':            request.env.user.id,
            })
        if result_list:
            FinanceTransaction.with_user(user_id).create(result_list)

            request.env['import.transactions'].sudo().create({
                'name':              f"Import {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                'transaction_count': len(result_list),
                'file':              base64.b64encode(decrypted.getvalue()).decode() if decrypted else None,
                'type_id':           import_type.id,
            })