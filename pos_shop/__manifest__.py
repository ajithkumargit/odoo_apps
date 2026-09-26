# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.


{
    'name': 'Custom POS Shop',
    'version': '1.1',
    'category': 'Sales/Point of Sale',
    'sequence': 6,
    'summary': '',
    'description': """""",
    'depends': ['point_of_sale',
        'sale',
        'sale_management',
        'sale_project'
    ],
    'data': [
        "views/sale_view.xml"
    ],
    'installable': True,
    'auto_install': True,
    'assets': {
        'web.assets_backend':[
            'pos_shop/static/src/css/custom_sheet.css'
        ]
    },
    'author': 'Ajith_Kumar',
    'license': 'LGPL-3',
}
