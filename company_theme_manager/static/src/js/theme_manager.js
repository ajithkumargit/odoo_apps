/** @odoo-module **/

import { registry } from "@web/core/registry";
import { browser } from "@web/core/browser/browser";


async function loadCompanyTheme(env) {
    try {
        const theme = await env.services.orm.call(
            "company.theme",
            "get_current_theme",
            []
        );

        if (!theme || !theme.id) {
            return;
        }

        const root = document.documentElement;

        const variables = {

            // General
            "--ctm-primary": theme.primary_color,
            "--ctm-secondary": theme.secondary_color,
            "--ctm-accent": theme.accent_color,

            "--ctm-success": theme.success_color,
            "--ctm-warning": theme.warning_color,
            "--ctm-danger": theme.danger_color,
            "--ctm-info": theme.info_color,

            "--ctm-background": theme.background_color,
            "--ctm-text": theme.text_color,

            // Navbar
            "--ctm-navbar": theme.navbar_color,
            "--ctm-navbar-text": theme.navbar_text_color,

            // Apps menu
            "--ctm-apps-menu": theme.apps_menu_color,
            "--ctm-apps-menu-text": theme.apps_menu_text_color,
            "--ctm-apps-menu-hover": theme.apps_menu_hover_color,
            "--ctm-apps-menu-icon": theme.apps_menu_icon_color,

            // Page
            "--ctm-page-background":
                theme.page_background_color,

            "--ctm-content-background":
                theme.content_background_color,

            // Sidebar
            "--ctm-sidebar": theme.sidebar_color,
            "--ctm-sidebar-text": theme.sidebar_text_color,
            "--ctm-sidebar-hover": theme.sidebar_hover_color,
            "--ctm-sidebar-active": theme.sidebar_active_color,

            // Dropdown
            "--ctm-dropdown": theme.dropdown_color,
            "--ctm-dropdown-text": theme.dropdown_text_color,
            "--ctm-dropdown-hover": theme.dropdown_hover_color,

            // Cards
            "--ctm-card": theme.card_color,
            "--ctm-card-border": theme.card_border_color,

            // Typography
            "--ctm-font": theme.font_family,
            "--ctm-font-size": `${theme.font_size}px`,

            // Components
            "--ctm-button-radius":
                `${theme.button_radius}px`,

            "--ctm-input-radius":
                `${theme.input_radius}px`,

            "--ctm-card-radius":
                `${theme.card_radius}px`,

            "--ctm-badge-radius":
                `${theme.badge_radius}px`,

            "--ctm-navbar": theme.navbar_color,
            "--ctm-navbar-text": theme.navbar_text_color,
            "--ctm-navbar-hover": theme.navbar_hover_color,
            "--ctm-navbar-active": theme.navbar_active_color,
        };
        for (const [variable, value] of Object.entries(variables)) {
            if (value) {
                root.style.setProperty(variable, value);
            }
        }

        root.dataset.companyTheme = theme.id;

    } catch (error) {
        console.error(
            "Company Theme Manager:",
            error
        );
    }
}


const companyThemeService = {
    dependencies: ["orm"],

    async start(env) {
        await loadCompanyTheme(env);

        return {
            reload: () => loadCompanyTheme(env),
        };
    },
};


registry
    .category("services")
    .add(
        "company_theme_manager",
        companyThemeService
    );