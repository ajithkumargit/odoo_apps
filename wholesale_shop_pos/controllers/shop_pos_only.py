from odoo import http
from odoo.http import request

from odoo.addons.wholesale_shop_pos.models.shop_pos_session_access import open_sessions_only


class ShopPosOnly(http.Controller):
    @http.route('/shop/pos-only', type='http', auth='user')
    def pos_only(self, **kw):
        if not open_sessions_only(request.env):
            return request.redirect('/store')
        configs = request.env['pos.config'].search([
            ('active', '=', True),
            ('company_id', 'in', request.env.user.company_ids.ids),
        ])
        sessions = request.env['pos.session'].sudo().search([
            ('config_id', 'in', configs.ids),
            ('state', '=', 'opened'),
            ('rescue', '=', False),
        ], order='id desc')
        if len(sessions) == 1:
            return request.redirect('/pos/ui/%s' % sessions.config_id.id)
        response = request.render('wholesale_shop_pos.shop_pos_only_landing', {
            'sessions': sessions,
        })
        response.headers['Cache-Control'] = 'no-store'
        return response
