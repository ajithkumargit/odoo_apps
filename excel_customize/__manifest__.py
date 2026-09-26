# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.


{
    'name': 'Excel Customize',
    'version': '1.1',
    'category': 'Human Resources/Expenses',
    'sequence': 6,
    'summary': '',
    'description': """""",
    'depends': ['base'
    ],
    'data': [
        "security/ir.model.access.csv",
        "views/excel_data.xml"
    ],
    'installable': True,
    'auto_install': False,
    'assets': {
    },
    'author': 'auFish',
    'license': 'LGPL-3',
}
