from odoo import http
from odoo.http import request
from odoo.addons.web.controllers.webmanifest import WebManifest


class StoreManifest(WebManifest):
    def _get_webmanifest(self):
        # Older installed shortcuts may still request this URL for metadata.
        manifest = super()._get_webmanifest()
        manifest.update({
            'name': 'Wholesale Shop', 'short_name': 'Wholesale Shop',
            'id': '/odoo', 'start_url': '/store', 'scope': '/',
            'icons': [{'src': '/wholesale_shop_pos/static/src/img/store.svg',
                       'sizes': 'any', 'type': 'image/svg+xml'}],
        })
        return manifest

    @http.route()
    def scoped_app(self, app_id=None, path='', app_name=''):
        return request.redirect('/store')
