/** @odoo-module **/
import { Component, onWillDestroy, useRef, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { loadJS } from "@web/core/assets";
import { _t } from "@web/core/l10n/translation";
import { Dialog } from "@web/core/dialog/dialog";
import { BarcodeVideoScanner, isBarcodeScannerSupported } from "@web/core/barcode/barcode_video_scanner";
import { CharField, charField } from "@web/views/fields/char/char_field";

export class ProductBarcodeDialog extends Component {
    static template = "wholesale_shop_pos.ProductBarcodeDialog";
    static components = { Dialog, BarcodeVideoScanner };
    static props = { close: Function, onResult: Function };
    setup() {
        this.state = useState({ live: isBarcodeScannerSupported(), busy: false, error: "" });
        this.photo = useRef("photo");
        onWillDestroy(() => { this.destroyed = true; });
    }
    async onResult(code) {
        if (this.done || this.destroyed || !code) return;
        this.done = true;
        try {
            await this.props.onResult(String(code));
            if (!this.destroyed) this.props.close();
        } catch (error) {
            this.done = false;
            if (!this.destroyed) this.state.error = _t("Could not set the barcode. Please try again.");
            throw error;
        }
    }
    onError() {
        this.state.live = false;
        this.state.error = _t("Live camera is unavailable. Take a photo or choose a barcode image below.");
    }
    async scanPhoto(event) {
        const input = event.target;
        const file = input.files?.[0];
        if (!file || this.state.busy) return;
        this.state.busy = true;
        this.state.error = "";
        let reader, url, code;
        try {
            await loadJS("/web/static/lib/zxing-library/zxing-library.js");
            if (this.destroyed) return;
            url = URL.createObjectURL(file);
            reader = new window.ZXing.BrowserMultiFormatReader(new Map([[window.ZXing.DecodeHintType.TRY_HARDER, true]]));
            code = (await reader.decodeFromImageUrl(url)).getText();
        } catch {
            if (!this.destroyed) this.state.error = _t("Barcode not found. Use a clear, close-up image showing the entire barcode.");
        } finally {
            reader?.reset();
            if (url) URL.revokeObjectURL(url);
            input.value = "";
            if (!this.destroyed) this.state.busy = false;
        }
        if (code) await this.onResult(code);
    }
}

export class ProductBarcodeField extends CharField {
    static template = "wholesale_shop_pos.ProductBarcodeField";
    setup() {
        super.setup();
        this.dialog = useService("dialog");
        onWillDestroy(() => { this.destroyed = true; });
    }
    scan() {
        this.dialog.add(ProductBarcodeDialog, {
            onResult: async (code) => {
                if (!this.destroyed && !this.props.readonly) {
                    await this.props.record.update({ [this.props.name]: code });
                }
            },
        });
    }
}
registry.category("fields").add("shop_barcode_scan", { ...charField, component: ProductBarcodeField });
