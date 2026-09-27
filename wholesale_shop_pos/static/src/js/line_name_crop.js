/** @odoo-module **/
import { Component, onWillDestroy, onWillStart, useRef, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { CharField, charField } from "@web/views/fields/char/char_field";
import { isBinarySize } from "@web/core/utils/binary";

export class LineNameCropDialog extends Component {
    static template = "wholesale_shop_pos.LineNameCropDialog";
    static components = { Dialog };
    static props = { close: Function, importId: Number, localSources: Array, onApply: Function };
    setup() {
        this.orm = useService("orm");
        this.image = useRef("image");
        this.file = useRef("file");
        this.state = useState({ sources: [], index: 0, ready: false, crop: null, busy: false, text: "", error: "" });
        this.uploadUrls = [];
        onWillStart(async () => {
            try {
                const saved = await this.orm.call("shop.purchase.import", "get_product_name_crop_sources", [[this.props.importId]]);
                this.state.sources = [...this.props.localSources, ...saved.filter(s => !this.props.localSources.some(local => local.key === s.key))];
            } catch (error) {
                this.state.error = error.data?.message || _t("Could not load bill images. You can upload a line photo below.");
            }
        });
        onWillDestroy(() => { this.destroyed = true; this.uploadUrls.forEach(url => URL.revokeObjectURL(url)); });
    }
    get source() { return this.state.sources[this.state.index]; }
    get selectionStyle() {
        const c = this.state.crop;
        return c ? `left:${c.x * 100}%;top:${c.y * 100}%;width:${c.w * 100}%;height:${c.h * 100}%;` : "display:none";
    }
    reset() { this.state.ready = false; this.state.crop = null; this.state.text = ""; this.state.error = ""; this.drag = null; }
    select(event) { this.reset(); this.state.index = Number(event.target.value); }
    upload(event) {
        const file = event.target.files?.[0];
        if (!file || this.state.busy) return;
        if (!file.type.startsWith("image/")) { this.state.error = _t("Choose an image of the product name."); return; }
        this.reset();
        const url = URL.createObjectURL(file);
        this.uploadUrls.push(url);
        this.state.sources.push({ name: file.name, url });
        this.state.index = this.state.sources.length - 1;
        event.target.value = "";
    }
    point(event) {
        const r = this.image.el.getBoundingClientRect();
        return { x: Math.max(0, Math.min(1, (event.clientX-r.left)/r.width)), y: Math.max(0, Math.min(1, (event.clientY-r.top)/r.height)) };
    }
    start(event) {
        if (!this.state.ready || this.state.busy || event.button !== 0) return;
        event.preventDefault(); this.drag = this.point(event); this.state.text = ""; this.state.error = "";
        this.state.crop = { ...this.drag, w: 0, h: 0 };
        event.currentTarget.setPointerCapture(event.pointerId);
    }
    move(event) {
        if (!this.drag) return;
        const p = this.point(event), a = this.drag;
        this.state.crop = { x: Math.min(a.x,p.x), y: Math.min(a.y,p.y), w: Math.abs(p.x-a.x), h: Math.abs(p.y-a.y) };
    }
    end() { this.drag = null; }
    whole() { this.state.crop = { x: 0, y: 0, w: 1, h: 1 }; this.state.text = ""; }
    async extract() {
        const c = this.state.crop, img = this.image.el;
        if (!c || !this.state.ready || this.state.busy) return;
        const x = Math.floor(c.x*img.naturalWidth), y = Math.floor(c.y*img.naturalHeight);
        const w = Math.floor(c.w*img.naturalWidth), h = Math.floor(c.h*img.naturalHeight);
        if (w < 8 || h < 8) { this.state.error = _t("Select a larger area around the product name."); return; }
        this.state.busy = true; this.state.error = ""; this.state.text = "";
        try {
            const canvas = document.createElement("canvas"), scale = Math.min(1, 2200/Math.max(w,h));
            canvas.width = Math.max(1, Math.round(w*scale)); canvas.height = Math.max(1, Math.round(h*scale));
            canvas.getContext("2d").drawImage(img, x,y,w,h, 0,0,canvas.width,canvas.height);
            const result = await this.orm.call("shop.purchase.import", "extract_cropped_product_name", [[this.props.importId], canvas.toDataURL("image/png").split(",")[1]]);
            if (!this.destroyed) this.state.text = result.text;
        } catch (error) {
            if (!this.destroyed) this.state.error = error.data?.message || _t("Could not read this crop. Try a clearer photo.");
        } finally { if (!this.destroyed) this.state.busy = false; }
    }
    async apply() {
        if (!this.state.text.trim() || this.state.busy) return;
        this.state.busy = true;
        try { await this.props.onApply(this.state.text.trim()); if (!this.destroyed) this.props.close(); }
        finally { if (!this.destroyed) this.state.busy = false; }
    }
}

export class LineNameCropField extends CharField {
    static template = "wholesale_shop_pos.LineNameCropField";
    setup() {
        super.setup(); this.dialog = useService("dialog"); this.notification = useService("notification");
        onWillDestroy(() => { this.destroyed = true; });
    }
    get canCrop() {
        const root = this.props.record.model.root;
        if (root?.resModel === "shop.purchase.import") return ["draft", "review"].includes(root.data.state);
        if (root?.resModel === "purchase.order") return ["draft", "sent"].includes(root.data.state);
        return !this.props.readonly;
    }
    crop() {
        if (!this.canCrop) return;
        const root = this.props.record.model.root, relation = this.props.record.data.import_id || root?.data?.shop_purchase_import_id;
        const id = root?.resModel === "shop.purchase.import" ? root.resId : (relation?.id || relation?.[0]);
        if (!id) { this.notification.add(_t("Save the bill import first, then crop the product name."), { type: "warning" }); return; }
        const localSources = [];
        if (root?.resModel === "shop.purchase.import") {
            const add = (value, name, key) => {
                if (value && !isBinarySize(value)) {
                    const type = value[0] === "i" ? "png" : value[0] === "U" ? "webp" : value[0] === "R" ? "gif" : "jpeg";
                    localSources.push({ name, key, url: `data:image/${type};base64,${value}` });
                }
            };
            if (root.data.original_is_image) add(root.data.original_file, root.data.original_file_name || _t("First page"), `shop.purchase.import:${id}`);
            for (const page of root.data.page_ids?.records || []) {
                if (page.data.is_image) add(page.data.page_file, page.data.page_file_name || _t("Bill page"), `shop.purchase.import.page:${page.resId || page.id}`);
            }
        }
        this.dialog.add(LineNameCropDialog, { importId: id, localSources, onApply: async text => {
            if (!this.destroyed && this.canCrop) await this.props.record.update({ [this.props.name]: text });
        } });
    }
}
registry.category("fields").add("line_name_crop", { ...charField, component: LineNameCropField });
