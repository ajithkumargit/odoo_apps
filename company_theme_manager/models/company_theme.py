from odoo import api, fields, models


class CompanyTheme(models.Model):
    _name = "company.theme"
    _description = "Company Theme"
    _order = "sequence, name"

    name = fields.Char(
        string="Theme Name",
        required=True,
    )

    sequence = fields.Integer(
        default=10,
    )

    active = fields.Boolean(
        default=True,
    )

    company_id = fields.Many2one(
        "res.company",
        string="Company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )

    primary_color = fields.Char(
        string="Primary Color",
        default="#714B67",
    )

    secondary_color = fields.Char(
        string="Secondary Color",
        default="#FFFFFF",
    )

    accent_color = fields.Char(
        string="Accent Color",
        default="#017E84",
    )

    success_color = fields.Char(
        string="Success Color",
        default="#28A745",
    )

    warning_color = fields.Char(
        string="Warning Color",
        default="#FFC107",
    )

    danger_color = fields.Char(
        string="Danger Color",
        default="#DC3545",
    )

    info_color = fields.Char(
        string="Info Color",
        default="#17A2B8",
    )


    background_color = fields.Char(
        string="Background",
        default="#F8F9FA",
    )

    text_color = fields.Char(
        string="Text",
        default="#212529",
    )


    navbar_color = fields.Char(
        string="Navbar Background",
        default="#714B67",
    )

    navbar_text_color = fields.Char(
        string="Navbar Text",
        default="#FFFFFF",
    )


    sidebar_color = fields.Char(
        string="Sidebar Background",
        default="#FFFFFF",
    )

    sidebar_text_color = fields.Char(
        string="Sidebar Text",
        default="#212529",
    )

    card_color = fields.Char(
        string="Card Background",
        default="#FFFFFF",
    )

    card_border_color = fields.Char(
        string="Card Border",
        default="#DEE2E6",
    )

    card_shadow = fields.Boolean(
        string="Card Shadow",
        default=True,
    )


    font_family = fields.Selection(
        [
            ("Inter", "Inter"),
            ("Roboto", "Roboto"),
            ("Arial", "Arial"),
            ("Open Sans", "Open Sans"),
            ("Lato", "Lato"),
            ("Poppins", "Poppins"),
            ("Montserrat", "Montserrat"),
            ("system-ui", "System UI"),
        ],
        string="Font Family",
        default="Inter",
    )

    font_size = fields.Integer(
        string="Base Font Size",
        default=14,
    )

    heading_font_weight = fields.Selection(
        [
            ("400", "Normal"),
            ("500", "Medium"),
            ("600", "Semi Bold"),
            ("700", "Bold"),
            ("800", "Extra Bold"),
        ],
        string="Heading Weight",
        default="600",
    )

    button_radius = fields.Integer(
        string="Button Radius",
        default=6,
    )

    input_radius = fields.Integer(
        string="Input Radius",
        default=5,
    )

    card_radius = fields.Integer(
        string="Card Radius",
        default=8,
    )

    badge_radius = fields.Integer(
        string="Badge Radius",
        default=20,
    )
    button_primary_text_color = fields.Char(
        string="Primary Button Text",
        default="#FFFFFF",
    )

    button_secondary_color = fields.Char(
        string="Secondary Button",
        default="#6C757D",
    )

    apps_menu_color = fields.Char(
        string="Apps Menu Background",
        default="#FFFFFF",
    )

    apps_menu_text_color = fields.Char(
        string="Apps Menu Text",
        default="#212529",
    )

    apps_menu_hover_color = fields.Char(
        string="Apps Menu Hover",
        default="#F5F5F5",
    )

    apps_menu_icon_color = fields.Char(
        string="Apps Menu Icon",
        default="#212529",
    )

    page_background_color = fields.Char(
        string="Page Background",
        default="#F8F9FA",
    )

    content_background_color = fields.Char(
        string="Content Background",
        default="#FFFFFF",
    )

    sidebar_hover_color = fields.Char(
        string="Sidebar Hover",
        default="#F0F0F0",
    )

    sidebar_active_color = fields.Char(
        string="Sidebar Active",
        default="#E8F5E9",
    )
    dropdown_color = fields.Char(
        string="Dropdown Background",
        default="#FFFFFF",
    )

    dropdown_text_color = fields.Char(
        string="Dropdown Text",
        default="#212529",
    )

    dropdown_hover_color = fields.Char(
        string="Dropdown Hover",
        default="#F5F5F5",
    )

    navbar_hover_color = fields.Char(
        string="Navbar Hover",
        default="#157347",
    )

    navbar_active_color = fields.Char(
        string="Navbar Active",
        default="#157347",
    )

    @api.constrains("font_size")
    def _check_font_size(self):
        for record in self:
            if record.font_size < 8 or record.font_size > 32:
                raise ValueError(
                    "Font size must be between 8 and 32."
                )

    @api.model
    def get_current_theme(self):
        company = self.env.company
        theme = company.theme_id

        # Fallback if no theme is selected
        if not theme:
            theme = self.search(
                [
                    ("company_id", "=", company.id),
                    ("active", "=", True),
                ],
                order="sequence, id",
                limit=1,
            )

        if not theme:
            return {}

        return {
            "id": theme.id,
            "name": theme.name,

            "primary_color": theme.primary_color,
            "secondary_color": theme.secondary_color,
            "accent_color": theme.accent_color,

            "success_color": theme.success_color,
            "warning_color": theme.warning_color,
            "danger_color": theme.danger_color,
            "info_color": theme.info_color,

            "background_color": theme.background_color,
            "text_color": theme.text_color,

            "navbar_color": theme.navbar_color,
            "navbar_text_color": theme.navbar_text_color,

            "sidebar_color": theme.sidebar_color,
            "sidebar_text_color": theme.sidebar_text_color,

            "card_color": theme.card_color,
            "card_border_color": theme.card_border_color,
            "card_shadow": theme.card_shadow,

            "font_family": theme.font_family,
            "font_size": theme.font_size,
            "heading_font_weight": theme.heading_font_weight,

            "button_radius": theme.button_radius,
            "input_radius": theme.input_radius,
            "card_radius": theme.card_radius,
            "badge_radius": theme.badge_radius,

            "button_primary_text_color":
                theme.button_primary_text_color,

            "button_secondary_color":
                theme.button_secondary_color,

            "apps_menu_color": theme.apps_menu_color,
            "apps_menu_text_color": theme.apps_menu_text_color,
            "apps_menu_hover_color": theme.apps_menu_hover_color,
            "apps_menu_icon_color": theme.apps_menu_icon_color,

            "page_background_color": theme.page_background_color,
            "content_background_color": theme.content_background_color,

            "sidebar_color": theme.sidebar_color,
            "sidebar_text_color": theme.sidebar_text_color,
            "sidebar_hover_color": theme.sidebar_hover_color,
            "sidebar_active_color": theme.sidebar_active_color,

            "dropdown_color": theme.dropdown_color,
            "dropdown_text_color": theme.dropdown_text_color,
            "dropdown_hover_color": theme.dropdown_hover_color,

            "navbar_color": theme.navbar_color,
            "navbar_text_color": theme.navbar_text_color,
            "navbar_hover_color": theme.navbar_hover_color,
            "navbar_active_color": theme.navbar_active_color,
        }