/** @odoo-module **/

import { Component, onWillDestroy, useRef, useState } from "@odoo/owl";
import { loadJS } from "@web/core/assets";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";
import { BarcodeVideoScanner, isBarcodeScannerSupported } from "@web/core/barcode/barcode_video_scanner";
import { Navbar } from "@point_of_sale/app/components/navbar/navbar";
import { ProductScreen } from "@point_of_sale/app/screens/product_screen/product_screen";
import { TicketScreen } from "@point_of_sale/app/screens/ticket_screen/ticket_screen";

// Keep live scanning on secure connections; decode a captured image otherwise.
export class PosBarcodeCamera extends Component {
    static template = "wholesale_shop_pos.PosBarcodeCamera";
    static components = { BarcodeVideoScanner };
    static props = BarcodeVideoScanner.props;

    setup() {
        this.liveSupported = isBarcodeScannerSupported();
        this.photoInput = useRef("photoInput");
        this.state = useState({ busy: false, error: "" });
        onWillDestroy(() => { this.destroyed = true; });
    }

    takePhoto() {
        this.photoInput.el.click();
    }

    async scanPhoto(event) {
        const input = event.target;
        const file = input.files?.[0];
        if (!file || this.state.busy) return;
        this.state.busy = true;
        this.state.error = "";
        let url;
        let reader;
        let code;
        try {
            await loadJS("/web/static/lib/zxing-library/zxing-library.js");
            if (this.destroyed) return;
            url = URL.createObjectURL(file);
            const hints = new Map([[window.ZXing.DecodeHintType.TRY_HARDER, true]]);
            reader = new window.ZXing.BrowserMultiFormatReader(hints);
            const result = await reader.decodeFromImageUrl(url);
            code = result.getText();
        } catch {
            if (!this.destroyed) {
                this.state.error = _t("Could not read the barcode. Take a sharp, close-up photo with the entire barcode visible, then try again.");
            }
        } finally {
            reader?.reset();
            if (url) URL.revokeObjectURL(url);
            input.value = "";
            if (!this.destroyed) this.state.busy = false;
        }
        if (code && !this.destroyed) {
            await this.props.onResult(code);
        }
    }
}

patch(Navbar.prototype, {
    setup() {
        super.setup(...arguments);
        this.isBarcodeScannerSupported = () => true;
    },
});
patch(ProductScreen, {
    components: { ...ProductScreen.components, BarcodeVideoScanner: PosBarcodeCamera },
});
patch(TicketScreen, {
    components: { ...TicketScreen.components, BarcodeVideoScanner: PosBarcodeCamera },
});
