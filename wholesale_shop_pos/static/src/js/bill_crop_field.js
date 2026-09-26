/** @odoo-module **/

import { registry } from "@web/core/registry";
import { imageUrl } from "@web/core/utils/urls";
import { isBinarySize } from "@web/core/utils/binary";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { Component, useRef, useState } from "@odoo/owl";

export class BillCropField extends Component {
    static template = "wholesale_shop_pos.BillCropField";
    static props = { ...standardFieldProps };

    setup() {
        this.image = useRef("image");
        this.state = useState({ dragging: false, startX: 0, startY: 0, x: 0, y: 0 });
    }

    get url() {
        const value = this.props.record.data[this.props.name];
        if (!value) {
            return "/web/static/img/placeholder.png";
        }
        if (isBinarySize(value)) {
            return imageUrl(this.props.record.resModel, this.props.record.resId, this.props.name, {
                unique: this.props.record.data.write_date,
            });
        }
        const kind = value[0] === "i" ? "png" : value[0] === "U" ? "webp" : "jpeg";
        return `data:image/${kind};base64,${value}`;
    }

    get selectionStyle() {
        const data = this.props.record.data;
        const left = this.state.dragging ? Math.min(this.state.startX, this.state.x) : data.crop_left;
        const top = this.state.dragging ? Math.min(this.state.startY, this.state.y) : data.crop_top;
        const right = this.state.dragging ? Math.max(this.state.startX, this.state.x) : data.crop_right;
        const bottom = this.state.dragging ? Math.max(this.state.startY, this.state.y) : data.crop_bottom;
        return `left:${left}%;top:${top}%;width:${right-left}%;height:${bottom-top}%;`;
    }

    point(event) {
        const rect = this.image.el.getBoundingClientRect();
        return {
            x: Math.max(0, Math.min(100, ((event.clientX - rect.left) / rect.width) * 100)),
            y: Math.max(0, Math.min(100, ((event.clientY - rect.top) / rect.height) * 100)),
        };
    }

    start(event) {
        event.preventDefault();
        const point = this.point(event);
        this.state.dragging = true;
        this.state.startX = this.state.x = point.x;
        this.state.startY = this.state.y = point.y;
        event.currentTarget.setPointerCapture(event.pointerId);
    }

    move(event) {
        if (!this.state.dragging) return;
        const point = this.point(event);
        this.state.x = point.x;
        this.state.y = point.y;
    }

    end() {
        if (!this.state.dragging) return;
        this.state.dragging = false;
        const left = Math.min(this.state.startX, this.state.x);
        const top = Math.min(this.state.startY, this.state.y);
        const right = Math.max(this.state.startX, this.state.x);
        const bottom = Math.max(this.state.startY, this.state.y);
        if (right - left >= 2 && bottom - top >= 2) {
            this.props.record.update({ crop_left: left, crop_top: top, crop_right: right, crop_bottom: bottom });
        }
    }
}

registry.category("fields").add("bill_image_crop", {
    component: BillCropField,
    supportedTypes: ["binary"],
});
