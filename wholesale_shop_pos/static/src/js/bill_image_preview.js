/** @odoo-module **/
import { Component, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";
import { isBinarySize } from "@web/core/utils/binary";
import { imageUrl } from "@web/core/utils/urls";

export class BillImagePreview extends Component {
    static template = "wholesale_shop_pos.BillImagePreview";
    static props = standardWidgetProps;
    setup() {
        this.state = useState({ open: false, index: 0, zoom: 100, x: 20, y: 80 });
    }
    get pages() {
        const record = this.props.record;
        const pages = [];
        if (record.data.original_file && record.data.original_is_image) {
            pages.push({ record, field: "original_file", name: record.data.original_file_name || "First page" });
        }
        for (const page of record.data.page_ids?.records || []) {
            if (page.data.page_file && page.data.is_image) {
                pages.push({ record: page, field: "page_file", name: page.data.page_file_name || "Bill page" });
            }
        }
        return pages;
    }
    get currentIndex() { return Math.min(this.state.index, Math.max(0, this.pages.length - 1)); }
    get page() { return this.pages[this.currentIndex]; }
    get url() {
        const page = this.page;
        if (!page) return "";
        const value = page.record.data[page.field];
        if (isBinarySize(value)) {
            return imageUrl(page.record.resModel, page.record.resId, page.field, { unique: page.record.data.write_date || value });
        }
        const kind = value[0] === "i" ? "png" : value[0] === "U" ? "webp" : value[0] === "R" ? "gif" : "jpeg";
        return `data:image/${kind};base64,${value}`;
    }
    open() {
        this.state.x = Math.max(0, window.innerWidth - Math.min(520, window.innerWidth) - 20);
        this.state.y = Math.min(80, window.innerHeight / 4);
        this.state.open = true;
    }
    select(event) { this.state.index = Number(event.target.value); this.state.zoom = 100; }
    zoom(delta) { this.state.zoom = Math.max(50, Math.min(400, this.state.zoom + delta)); }
    start(event) {
        if (event.button !== 0 || event.target.closest("button")) return;
        this.drag = { x: event.clientX - this.state.x, y: event.clientY - this.state.y };
        event.currentTarget.setPointerCapture(event.pointerId);
        event.preventDefault();
    }
    move(event) {
        if (!this.drag) return;
        this.state.x = Math.max(0, Math.min(window.innerWidth - 100, event.clientX - this.drag.x));
        this.state.y = Math.max(0, Math.min(window.innerHeight - 45, event.clientY - this.drag.y));
    }
    onKeydown(event) {
        if (event.key === "Escape") {
            this.state.open = false;
            event.stopPropagation();
        }
    }
    end() { this.drag = null; }
}
registry.category("view_widgets").add("bill_image_preview", { component: BillImagePreview });
