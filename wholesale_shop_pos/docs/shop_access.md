# Shop access controls

After upgrading `wholesale_shop_pos`, assign **Open Sessions Only** under **Settings → Users → Access Rights → POS Session Visibility** to each cashier who must not start a register. These users see **Continue Selling** only for an opened POS session; otherwise the dashboard says **No session started**. POS Administrators and Settings Administrators are exempt. Remove the group to restore normal POS access.

Use **Settings → Wholesale Shop → Public Access** to change access to public database tools, self-signup, and password reset at any time. All three switches default to off. Invited users can still follow their private invitation link. The login page has no database selector, database manager, or debug superuser link.

Set the deployed Odoo service's `dbfilter` to the intended database, for example `^production_db$` when that is its database name. This lets Odoo select the right database before an anonymous request reaches the add-on's database-route controls. A reverse proxy should also restrict database management paths when serving multiple databases or when requests may arrive without a selected database. Keep the Odoo master password strong even when the public database-tools switch is off.

Restart Odoo after installing or upgrading this add-on so the controller and POS model Python code reload. Settings and user-group changes after that do not require another restart; users should refresh their browser after group changes.
