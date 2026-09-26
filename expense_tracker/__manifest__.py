# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.


{
    'name': 'Income/Expense Tracker',
    'version': '1.4',
    'category': 'Human Resources/Expenses',
    'sequence': 6,
    'summary': '',
    'description': """""",
    'depends': ['base', 'web'],
    'data': [
        "security/ir.model.access.csv",
        'data/classification_data.xml',
        "views/finance_transaction.xml",
        'views/category_balance.xml',
        'views/dashboard_menu.xml',
        'views/classification_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'expense_tracker/static/src/components/multi_select.js',
            'expense_tracker/static/src/components/multi_select.xml',

            'expense_tracker/static/src/js/dashboard.js',
            'expense_tracker/static/src/xml/dashboard.xml',
            'https://cdn.jsdelivr.net/npm/chart.js',
            
        ],
    },
    'installable': True,
    'auto_install': False,
    'author': 'auFish',
    'license': 'LGPL-3',
}
