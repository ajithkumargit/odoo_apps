/** @odoo-module **/
import { Component, onWillDestroy, onWillStart, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { ImageField, imageField } from "@web/views/fields/image/image_field";
import { FileUploader } from "@web/views/fields/file_handler";
import { browser } from "@web/core/browser/browser";

export class ProductImageSearchDialog extends Component {
    static template = "wholesale_shop_pos.ProductImageSearchDialog";
    static components = { Dialog, FileUploader };
    static props = { close: Function, query: String, onApply: Function };

    setup() {
        this.orm = useService("orm");
        this.state = useState({ query: this.props.query, results: [], busy: false, searched: false, error: "", automatic: false });
        onWillDestroy(() => { this.destroyed = true; });
        onWillStart(async () => {
            try {
                this.state.automatic = await this.orm.call("product.template", "shop_image_search_available", []);
            } catch (error) {
                this.state.error = error.data?.message || _t("Automatic search is unavailable. You can upload a photo.");
            }
        });
    }

    openGoogle() {
        if (!this.state.query.trim()) return;
        browser.open(`https://www.google.com/search?udm=2&q=${encodeURIComponent(this.state.query.trim())}`, "_blank", "noopener,noreferrer");
    }

    async upload(info) {
        if (!info.data || this.destroyed || this.state.busy) return;
        this.state.busy = true;
        this.state.error = "";
        try {
            await this.props.onApply(info.data);
            if (!this.destroyed) this.props.close();
        } catch (error) {
            if (!this.destroyed) this.state.error = error.data?.message || _t("This photo could not be applied.");
        } finally {
            if (!this.destroyed) this.state.busy = false;
        }
    }

    async search() {
        if (this.state.busy || !this.state.query.trim()) return;
        if (!this.state.automatic) { this.openGoogle(); return; }
        this.state.busy = true;
        this.state.error = "";
        this.state.results = [];
        this.state.searched = false;
        try {
            const results = await this.orm.call("product.template", "shop_search_product_images", [this.state.query.trim()]);
            if (!this.destroyed) {
                this.state.results = results;
                this.state.searched = true;
            }
        } catch (error) {
            if (!this.destroyed) this.state.error = error.data?.message || _t("Image search failed. Please try again.");
        } finally {
            if (!this.destroyed) this.state.busy = false;
        }
    }

    async choose(result) {
        if (this.state.busy) return;
        this.state.busy = true;
        this.state.error = "";
        try {
            const data = await this.orm.call("product.template", "shop_fetch_product_image", [result.url]);
            if (this.destroyed) return;
            await this.props.onApply(data);
            if (!this.destroyed) this.props.close();
        } catch (error) {
            if (!this.destroyed) this.state.error = error.data?.message || _t("This image could not be loaded. Choose another image.");
        } finally {
            if (!this.destroyed) this.state.busy = false;
        }
    }
}

export class ProductImageSearchField extends ImageField {
    static template = "wholesale_shop_pos.ProductImageSearchField";
    setup() {
        super.setup();
        this.dialog = useService("dialog");
        onWillDestroy(() => { this.destroyed = true; });
    }

    openImageSearch() {
        if (this.props.readonly) return;
        this.dialog.add(ProductImageSearchDialog, {
            query: this.props.record.data.name || "",
            onApply: async data => {
                if (this.destroyed || this.props.readonly) return;
                await this.props.record.update({ [this.props.name]: data });
                this.state.isValid = true;
                this.lastURL = undefined;
            },
        });
    }
}
registry.category("fields").add("shop_product_image", { ...imageField, component: ProductImageSearchField });
