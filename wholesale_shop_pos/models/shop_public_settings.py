from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    shop_allow_public_database_tools = fields.Boolean(
        string='Public database tools',
        config_parameter='wholesale_shop_pos.allow_public_database_tools',
    )
    shop_allow_public_signup = fields.Boolean(
        string='Public self-signup',
        config_parameter='wholesale_shop_pos.allow_public_signup',
    )
    shop_allow_public_password_reset = fields.Boolean(
        string='Public password reset',
        config_parameter='wholesale_shop_pos.allow_public_password_reset',
    )

    def set_values(self):
        result = super().set_values()
        params = self.env['ir.config_parameter'].sudo()
        params.set_param('auth_signup.invitation_scope', 'b2c' if self.shop_allow_public_signup else 'b2b')
        params.set_param('auth_signup.reset_password', 'True' if self.shop_allow_public_password_reset else 'False')
        return result
