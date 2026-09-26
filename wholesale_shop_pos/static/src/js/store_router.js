/** @odoo-module **/
import { router, startRouter } from "@web/core/browser/router";
import { patch } from "@web/core/utils/patch";

patch(router, {
    stateToUrl(state) {
        return super.stateToUrl(state).replace(/^\/odoo(?=\/|\?|#|$)/, "/store");
    },
    urlToState(url) {
        if (url.pathname === "/store" || url.pathname.startsWith("/store/")) {
            const compatible = new URL(url.href);
            compatible.pathname = "/odoo" + url.pathname.slice(6);
            return super.urlToState(compatible);
        }
        return super.urlToState(url);
    },
});
// The core router initializes at import time, before this patch is applied.
// Reparse deep links now, before the web client starts loading its action.
startRouter();
