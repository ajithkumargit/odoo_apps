from odoo import api, fields, models, _
from odoo.exceptions import AccessError


def open_sessions_only(env):
    user = env.user
    return (user.has_group('wholesale_shop_pos.group_shop_pos_open_sessions_only')
            and not user.has_group('base.group_system')
            and not user.has_group('point_of_sale.group_pos_manager'))


class PosConfig(models.Model):
    _inherit = 'pos.config'

    shop_open_sessions_only = fields.Boolean(compute='_compute_shop_open_sessions_only')

    @api.depends_context('uid')
    def _compute_shop_open_sessions_only(self):
        restricted = open_sessions_only(self.env)
        for config in self:
            config.shop_open_sessions_only = restricted

    def open_ui(self):
        if open_sessions_only(self.env) and any(
            config.current_session_state != 'opened' for config in self
        ):
            raise AccessError(_('No session started. Ask an administrator to open this POS register.'))
        return super().open_ui()


class PosSession(models.Model):
    _inherit = 'pos.session'

    @api.model_create_multi
    def create(self, vals_list):
        if open_sessions_only(self.env):
            raise AccessError(_('Only an administrator can start a POS session for this user.'))
        return super().create(vals_list)
