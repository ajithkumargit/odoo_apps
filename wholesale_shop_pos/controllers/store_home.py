from odoo import http
from odoo.http import request
from odoo.addons.web.controllers.home import Home

from odoo.addons.wholesale_shop_pos.models.shop_pos_session_access import open_sessions_only


def store_path(path):
    if path == '/odoo' or path.startswith(('/odoo/', '/odoo?', '/odoo#')):
        return '/store' + path[5:]
    return path


class StoreHome(Home):
    @http.route(['/web', '/odoo', '/odoo/<path:subpath>', '/store', '/store/<path:subpath>', '/scoped_app/<path:subpath>'])
    def web_client(self, s_action=None, **kw):
        if request.session.uid and open_sessions_only(
            request.env['res.users'].with_user(request.session.uid).env
        ):
            return request.redirect('/shop/pos-only')
        path = request.httprequest.path
        if path == '/odoo' or path.startswith('/odoo/'):
            return request.redirect_query(store_path(path), query=request.httprequest.args, code=302)
        return super().web_client(s_action=s_action, **kw)

    def _login_redirect(self, uid, redirect=None):
        return store_path(super()._login_redirect(uid, redirect=redirect))
