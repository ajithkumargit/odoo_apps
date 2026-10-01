from odoo import http
from odoo.http import request
from odoo.addons.auth_signup.controllers.main import AuthSignupHome
from odoo.addons.web.controllers.database import Database


def public_setting_enabled(name):
    if not request.db:
        return False
    return request.env['ir.config_parameter'].sudo().get_param(
        'wholesale_shop_pos.' + name, 'False'
    ) == 'True'


def database_tools_allowed():
    if public_setting_enabled('allow_public_database_tools'):
        return True
    if not request.db or not request.session.uid:
        return False
    user = request.env['res.users'].sudo().browse(request.session.uid)
    return bool(user.exists() and user.has_group('base.group_system'))


class ShopDatabase(Database):
    @http.route()
    def selector(self, **kw):
        return super().selector(**kw) if database_tools_allowed() else request.not_found()

    @http.route()
    def manager(self, **kw):
        return super().manager(**kw) if database_tools_allowed() else request.not_found()

    @http.route()
    def create(self, *args, **kw):
        return super().create(*args, **kw) if database_tools_allowed() else request.not_found()

    @http.route()
    def duplicate(self, *args, **kw):
        return super().duplicate(*args, **kw) if database_tools_allowed() else request.not_found()

    @http.route()
    def drop(self, *args, **kw):
        return super().drop(*args, **kw) if database_tools_allowed() else request.not_found()

    @http.route()
    def backup(self, *args, **kw):
        return super().backup(*args, **kw) if database_tools_allowed() else request.not_found()

    @http.route()
    def restore(self, *args, **kw):
        return super().restore(*args, **kw) if database_tools_allowed() else request.not_found()

    @http.route()
    def change_password(self, *args, **kw):
        return super().change_password(*args, **kw) if database_tools_allowed() else request.not_found()

    @http.route()
    def list(self):
        return super().list() if database_tools_allowed() else []


class ShopSignupHome(AuthSignupHome):
    def get_auth_signup_config(self):
        config = super().get_auth_signup_config()
        config['signup_enabled'] = bool(
            config['signup_enabled'] and public_setting_enabled('allow_public_signup')
        )
        config['reset_password_enabled'] = bool(
            config['reset_password_enabled'] and public_setting_enabled('allow_public_password_reset')
        )
        return config
