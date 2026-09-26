{
    "name": "Retail POS Bridge",
    "version": "19.0.1.0.0",
    "category": "Sales/Point of Sale",
    "summary": "Bridge custom Angular POS billing, pricing, barcode, and stock sync with Odoo.",
    "author": "Ajith Kumar",
    "license": "LGPL-3",
    "depends": [
        "point_of_sale",
        "product",
        "sale_management",
        "stock",
    ],
    "data": [
        "security/ir.model.access.csv",
        "views/product_template_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
