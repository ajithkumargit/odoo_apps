from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError


class ShopUserMenuAccess(models.Model):
    _inherit = 'res.users'

    shop_restrict_menus = fields.Boolean(
        string='Limit Visible Apps', groups='base.group_system', copy=False,
        help='Show only selected apps, subject to this user\'s normal access rights. Administrators keep all menus.',
    )
    shop_allowed_app_ids = fields.Many2many(
        'ir.ui.menu', 'shop_user_allowed_app_rel', 'user_id', 'menu_id',
        string='Visible Apps', groups='base.group_system', copy=False,
        domain=[('parent_id', '=', False)],
    )
    shop_hidden_menu_ids = fields.Many2many(
        'ir.ui.menu', 'shop_user_hidden_menu_rel', 'user_id', 'menu_id',
        string='Hidden Submenus / Screens', groups='base.group_system', copy=False,
        domain=[('parent_id', '!=', False)],
        help='Hide these menus and all their children inside the selected apps.',
    )

    @api.constrains('shop_allowed_app_ids')
    def _check_shop_allowed_apps(self):
        if self.shop_allowed_app_ids.filtered('parent_id'):
            raise ValidationError(_('Select top-level apps in Visible Apps. Use Hidden Submenus / Screens for child menus.'))

    def write(self, values):
        result = super().write(values)
        if {'shop_restrict_menus', 'shop_allowed_app_ids', 'shop_hidden_menu_ids'}.intersection(values):
            self.env.registry.clear_cache()
        return result

    def action_apply_shop_menu_preset(self):
        if not self.env.user.has_group('base.group_system'):
            raise AccessError(_('Only an administrator can configure menu access.'))
        roots = self.env['ir.ui.menu']
        for xmlid in (
            'wholesale_shop_pos.menu_wholesale_shop_root',
            'point_of_sale.menu_point_root',
            'account.menu_finance',
            'spreadsheet_dashboard.spreadsheet_dashboard_menu_root',
            'stock.menu_stock_root',
            'purchase.menu_purchase_root',
        ):
            menu = self.env.ref(xmlid, raise_if_not_found=False)
            if menu:
                roots |= menu
        self.write({
            'shop_restrict_menus': True,
            'shop_allowed_app_ids': [fields.Command.set(roots.ids)],
            'shop_hidden_menu_ids': [fields.Command.clear()],
        })
        return True


class ShopMenuVisibility(models.Model):
    _inherit = 'ir.ui.menu'

    @api.model
    def _visible_menu_ids(self, debug=False):
        visible = super()._visible_menu_ids(debug=debug)
        if self.env.su or self.env.user.has_group('base.group_system'):
            return visible
        user = self.env.user.sudo()
        if not user.shop_restrict_menus:
            return visible
        menus = self.sudo()
        allowed = menus.search([('id', 'child_of', user.shop_allowed_app_ids.ids)])
        if user.shop_hidden_menu_ids:
            allowed -= menus.search([('id', 'child_of', user.shop_hidden_menu_ids.ids)])
        # Apply this after the standard group/model-access check; the setting
        # can remove menus but cannot grant access the user does not have.
        return frozenset(visible.intersection(allowed.ids))
