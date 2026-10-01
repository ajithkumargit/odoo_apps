import json

from lxml import html

from odoo.exceptions import AccessError
from odoo.tests.common import HttpCase, TransactionCase


class TestShopAccess(TransactionCase):
    def test_pos_only_option_converts_portal_user_to_internal_pos_user(self):
        portal = self.env.ref('base.group_portal')
        user = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Portal POS Cashier',
            'login': 'portal.pos.cashier.test',
            'group_ids': [(6, 0, portal.ids)],
        })
        self.assertTrue(user.share)
        user.shop_pos_only_screen = True
        self.assertFalse(user.share)
        self.assertFalse(user.has_group('base.group_portal'))
        self.assertTrue(user.has_group('base.group_user'))
        self.assertTrue(user.has_group('point_of_sale.group_pos_user'))
        self.assertTrue(user.has_group('wholesale_shop_pos.group_shop_pos_open_sessions_only'))
        self.assertTrue(user.shop_pos_only_screen)
        user.shop_pos_only_screen = False
        self.assertFalse(user.has_group('wholesale_shop_pos.group_shop_pos_open_sessions_only'))

    def test_assigning_pos_only_group_converts_portal_user(self):
        portal = self.env.ref('base.group_portal')
        user = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Group Portal Cashier',
            'login': 'group.portal.cashier.test',
            'group_ids': [(6, 0, portal.ids)],
        })
        group = self.env.ref('wholesale_shop_pos.group_shop_pos_open_sessions_only')
        group.write({'user_ids': [(4, user.id)]})
        self.assertFalse(user.has_group('base.group_portal'))
        self.assertTrue(user.has_group('base.group_user'))
        self.assertTrue(user.has_group('point_of_sale.group_pos_user'))
        self.assertTrue(user.has_group('wholesale_shop_pos.group_shop_pos_open_sessions_only'))

    def test_open_sessions_only_group_cannot_start_session(self):
        group = self.env.ref('wholesale_shop_pos.group_shop_pos_open_sessions_only')
        cashier = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Open Session Cashier',
            'login': 'open.session.cashier.test',
            'group_ids': [(4, group.id)],
        })
        config = self.env['pos.config'].create({'name': 'Closed register test'})
        self.assertTrue(config.with_user(cashier).shop_open_sessions_only)
        self.assertFalse(config.shop_open_sessions_only)
        with self.assertRaisesRegex(AccessError, 'No session started'):
            config.with_user(cashier).open_ui()
        with self.assertRaises(AccessError):
            self.env['pos.session'].with_user(cashier).create({
                'config_id': config.id, 'user_id': cashier.id,
            })
        cashier.group_ids = [(4, self.env.ref('point_of_sale.group_pos_manager').id)]
        self.assertFalse(config.with_user(cashier).shop_open_sessions_only)

    def test_public_access_settings_are_changeable(self):
        settings = self.env['res.config.settings'].create({
            'shop_allow_public_database_tools': True,
            'shop_allow_public_signup': True,
            'shop_allow_public_password_reset': True,
        })
        settings.set_values()
        params = self.env['ir.config_parameter'].sudo()
        self.assertEqual(params.get_param('wholesale_shop_pos.allow_public_database_tools'), 'True')
        self.assertEqual(params.get_param('auth_signup.invitation_scope'), 'b2c')
        self.assertEqual(params.get_param('auth_signup.reset_password'), 'True')
        settings.write({
            'shop_allow_public_database_tools': False,
            'shop_allow_public_signup': False,
            'shop_allow_public_password_reset': False,
        })
        settings.set_values()
        self.assertFalse(params.get_param('wholesale_shop_pos.allow_public_database_tools'))
        self.assertEqual(params.get_param('auth_signup.invitation_scope'), 'b2b')
        self.assertEqual(params.get_param('auth_signup.reset_password'), 'False')


class TestShopPublicRoutes(HttpCase):
    def test_anonymous_database_tools_and_signup_are_hidden(self):
        for path in ('/web/database/manager', '/web/database/selector',
                     '/web/signup', '/web/reset_password'):
            response = self.url_open(path, allow_redirects=False)
            self.assertEqual(response.status_code, 404, path)
        response = self.url_open('/web/login', allow_redirects=False)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('Manage Databases', response.text)
        self.assertNotIn('href="/web/database/selector"', response.text)
        self.assertNotIn('Log in as superuser', response.text)
        self.assertNotIn('href="/web/signup', response.text)
        self.assertNotIn('href="/web/reset_password', response.text)
        response = self.url_open(
            '/web/database/list',
            data=json.dumps({'jsonrpc': '2.0', 'method': 'call', 'params': {}, 'id': 1}),
            headers={'Content-Type': 'application/json'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['result'], [])

    def test_pos_only_user_lands_on_pos_instead_of_portal(self):
        company = self.env['res.company'].create({'name': 'POS only empty company'})
        user = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'POS Only Login',
            'login': 'pos.only.login.test',
            'password': 'pos-only-test-password',
            'company_id': company.id,
            'company_ids': [(6, 0, company.ids)],
            'group_ids': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref('point_of_sale.group_pos_user').id,
                self.env.ref('wholesale_shop_pos.group_shop_pos_open_sessions_only').id,
            ])],
        })
        login_page = self.url_open('/web/login', allow_redirects=False)
        csrf_token = html.fromstring(login_page.text).xpath('//input[@name="csrf_token"]/@value')[0]
        login_response = self.url_open('/web/login', data={
            'login': user.login,
            'password': 'pos-only-test-password',
            'csrf_token': csrf_token,
        }, allow_redirects=False)
        self.assertEqual(login_response.status_code, 303)
        self.assertIn('/shop/pos-only', login_response.headers['Location'])
        response = self.url_open('/store', allow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertIn('/shop/pos-only', response.headers['Location'])
        response = self.url_open('/shop/pos-only', allow_redirects=False)
        self.assertEqual(response.status_code, 200)
        self.assertIn('No session started', response.text)
