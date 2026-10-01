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
    shop_pos_only_screen = fields.Boolean(
        string='POS Only Screen', compute='_compute_shop_pos_only_screen',
        inverse='_inverse_shop_pos_only_screen', groups='base.group_system',
        help='Open an active POS register directly after login, or show No session started. This also gives the user internal POS access and overrides their visible app selection.',
    )

    @api.depends('group_ids')
    def _compute_shop_pos_only_screen(self):
        group = self.env.ref('wholesale_shop_pos.group_shop_pos_open_sessions_only', raise_if_not_found=False)
        for user in self:
            user.shop_pos_only_screen = bool(group and group in user.group_ids)

    def _inverse_shop_pos_only_screen(self):
        restricted = self.env.ref('wholesale_shop_pos.group_shop_pos_open_sessions_only')
        portal = self.env.ref('base.group_portal')
        public = self.env.ref('base.group_public')
        internal = self.env.ref('base.group_user')
        pos_user = self.env.ref('point_of_sale.group_pos_user')
        for user in self:
            groups = user.group_ids
            if user.shop_pos_only_screen:
                groups = (groups - portal - public) | internal | pos_user | restricted
            else:
                groups -= restricted
            user.group_ids = [fields.Command.set(groups.ids)]

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
        if user.has_group('wholesale_shop_pos.group_shop_pos_open_sessions_only') and not user.has_group('point_of_sale.group_pos_manager'):
            pos_menu = self.env.ref('point_of_sale.menu_point_root')
            pos_menus = self.sudo().search([('id', 'child_of', pos_menu.id)])
            return frozenset(visible.intersection(pos_menus.ids))
        if not user.shop_restrict_menus:
            return visible
        menus = self.sudo()
        allowed = menus.search([('id', 'child_of', user.shop_allowed_app_ids.ids)])
        if user.shop_hidden_menu_ids:
            allowed -= menus.search([('id', 'child_of', user.shop_hidden_menu_ids.ids)])
        # Apply this after the standard group/model-access check; the setting
        # can remove menus but cannot grant access the user does not have.
        return frozenset(visible.intersection(allowed.ids))


class ShopPosOnlyGroup(models.Model):
    _inherit = 'res.groups'

    def write(self, values):
        restricted = self.env.ref('wholesale_shop_pos.group_shop_pos_open_sessions_only', raise_if_not_found=False)
        if restricted and restricted in self and 'user_ids' in values:
            assigned_ids = set()
            for command in values['user_ids']:
                if command[0] == fields.Command.SET:
                    assigned_ids.update(command[2])
                elif command[0] == fields.Command.LINK:
                    assigned_ids.add(command[1])
            portal = self.env.ref('base.group_portal')
            public = self.env.ref('base.group_public')
            internal = self.env.ref('base.group_user')
            for user in self.env['res.users'].browse(assigned_ids):
                if portal in user.group_ids or public in user.group_ids:
                    groups = (user.group_ids - portal - public) | internal
                    user.group_ids = [fields.Command.set(groups.ids)]
        return super().write(values)
