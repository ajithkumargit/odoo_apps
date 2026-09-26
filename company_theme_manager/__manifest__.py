{
    "name": "Company Theme Manager",
    "version": "19.0.1.0.0",
    "category": "Tools",
    "summary": "Dynamic company-specific Odoo themes",

    "author": "AJITHKUMAR",
    "website": "https://github.com/ajithkumargit/odoo_apps",
    "license": "LGPL-3",
    "depends": [
        "base",
        "web",
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/theme_data.xml",
        "views/company_theme_views.xml",
        "views/res_company_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "company_theme_manager/static/src/scss/company_theme.scss",
            "company_theme_manager/static/src/js/theme_manager.js",
        ],
    },
    "installable": True,
    "application": True,
}