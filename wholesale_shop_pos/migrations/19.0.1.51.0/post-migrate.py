from odoo import SUPERUSER_ID, api, fields


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    group = env.ref('wholesale_shop_pos.group_shop_pos_open_sessions_only', raise_if_not_found=False)
    if not group:
        return
    portal = env.ref('base.group_portal')
    public = env.ref('base.group_public')
    internal = env.ref('base.group_user')
    for user in group.user_ids:
        if portal in user.group_ids or public in user.group_ids or internal not in user.group_ids:
            groups = (user.group_ids - portal - public) | internal
            user.group_ids = [fields.Command.set(groups.ids)]
