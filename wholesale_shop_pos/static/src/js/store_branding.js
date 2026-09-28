/** @odoo-module **/
import { registry } from "@web/core/registry";
import { patch } from "@web/core/utils/patch";
import { titleService } from "@web/core/browser/title_service";
import { _t } from "@web/core/l10n/translation";
import { ErrorDialog, ClientErrorDialog, NetworkErrorDialog, RPCErrorDialog, WarningDialog } from "@web/core/errors/error_dialogs";
import "@web/core/pwa/pwa_service";
import "@web/webclient/user_menu/user_menu_items";

patch(titleService, {
    start(...args) {
        const service = super.start(...args);
        const refresh = () => {
            document.title = document.title.replace(/^(\(\d+\)\s*)?Odoo$/, (_, counter) => `${counter || ""}Wholesale Shop`);
        };
        for (const method of ["setParts", "setCounters"]) {
            const original = service[method];
            service[method] = (...values) => { const result = original(...values); refresh(); return result; };
        }
        refresh();
        return service;
    },
});

// Keep other PWA-service consumers working while disabling all install UI.
patch(registry.category("services").get("pwa"), {
    start(...args) {
        const state = super.start(...args);
        for (const key of ["isAvailable", "canPromptToInstall"]) {
            Object.defineProperty(state, key, { configurable: true, get: () => false, set: () => {} });
        }
        state.show = () => {};
        return state;
    },
});
window.addEventListener("beforeinstallprompt", event => event.preventDefault());
const menu = registry.category("user_menuitems");
menu.remove("install_pwa");
menu.remove("odoo_account");

ErrorDialog.title = _t("Application Error");
ClientErrorDialog.title = _t("Client Error");
NetworkErrorDialog.title = _t("Network Error");
patch(RPCErrorDialog.prototype, {
    inferTitle() {
        super.inferTitle();
        if (this.title) this.title = this.title.toString().replace(/^Odoo\s+/, "");
    },
});
patch(WarningDialog.prototype, {
    inferTitle() {
        return super.inferTitle().toString().replace(/^Odoo\s+/, "");
    },
});
