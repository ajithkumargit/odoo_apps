from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, new_test_user


class TestUserMenuAccess(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(cls.env, login='shop_menu_test_user', groups='base.group_user')
        cls.other = new_test_user(cls.env, login='shop_menu_test_other', groups='base.group_user')
        cls.root = cls.env.ref('wholesale_shop_pos.menu_wholesale_shop_root')
        cls.bill = cls.env.ref('wholesale_shop_pos.menu_shop_purchase_import')
        cls.discuss = cls.env.ref('mail.menu_root_discuss')

    def menus(self, user=None):
        return self.env['ir.ui.menu'].with_user(user or self.user)

    def test_preset_selects_six_apps(self):
        self.user.action_apply_shop_menu_preset()
        self.assertTrue(self.user.shop_restrict_menus)
        self.assertEqual(len(self.user.shop_allowed_app_ids), 6)
        self.assertIn(self.root, self.user.shop_allowed_app_ids)

    def test_allowlist_keeps_children_and_hides_other_apps(self):
        self.user.write({'shop_restrict_menus': True, 'shop_allowed_app_ids': [fields.Command.set(self.root.ids)]})
        visible = self.menus()._visible_menu_ids()
        self.assertIn(self.root.id, visible)
        self.assertIn(self.bill.id, visible)
        self.assertNotIn(self.discuss.id, visible)

    def test_settings_do_not_affect_users_with_same_groups(self):
        self.user.write({'shop_restrict_menus': True, 'shop_allowed_app_ids': [fields.Command.set(self.root.ids)]})
        self.assertNotIn(self.discuss.id, self.menus()._visible_menu_ids())
        self.assertIn(self.discuss.id, self.menus(self.other)._visible_menu_ids())

    def test_hidden_menu_and_children_are_removed(self):
        child = self.env['ir.ui.menu'].create({'name': 'Child bill screen', 'parent_id': self.bill.id, 'action': self.bill.action._name + ',' + str(self.bill.action.id)})
        self.user.write({'shop_restrict_menus': True,
                         'shop_allowed_app_ids': [fields.Command.set(self.root.ids)],
                         'shop_hidden_menu_ids': [fields.Command.set(self.bill.ids)]})
        visible = self.menus()._visible_menu_ids()
        self.assertNotIn(self.bill.id, visible)
        self.assertNotIn(child.id, visible)

    def test_loaded_menu_cache_is_refreshed_after_admin_edit(self):
        before = self.menus().load_menus(False)
        self.assertIn(self.discuss.id, before)
        self.user.write({'shop_restrict_menus': True, 'shop_allowed_app_ids': [fields.Command.set(self.root.ids)]})
        after = self.menus().load_menus(False)
        self.assertNotIn(self.discuss.id, after)
        self.assertIn(self.root.id, after)
        self.user.shop_restrict_menus = False
        self.assertIn(self.discuss.id, self.menus().load_menus(False))

    def test_empty_allowlist_hides_all_apps(self):
        self.user.shop_restrict_menus = True
        self.assertFalse(self.menus()._visible_menu_ids())

    def test_menu_setting_does_not_grant_group_access(self):
        settings = self.env.ref('base.menu_administration')
        self.user.write({'shop_restrict_menus': True, 'shop_allowed_app_ids': [fields.Command.set(settings.ids)]})
        self.assertNotIn(settings.id, self.menus()._visible_menu_ids())

    def test_nonadmin_cannot_change_visibility_settings(self):
        with self.assertRaises(AccessError):
            self.user.with_user(self.user).write({'shop_restrict_menus': True})
        with self.assertRaises(AccessError):
            self.user.with_user(self.user).action_apply_shop_menu_preset()

    def test_administrator_retains_normal_menus(self):
        admin = self.env.ref('base.user_admin')
        before = self.menus(admin)._visible_menu_ids()
        admin.write({'shop_restrict_menus': True, 'shop_allowed_app_ids': [fields.Command.clear()]})
        self.assertEqual(before, self.menus(admin)._visible_menu_ids())
