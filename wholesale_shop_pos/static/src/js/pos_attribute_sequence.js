/** @odoo-module **/

import { patch } from '@web/core/utils/patch';
import { ProductTemplateAttributeLine } from '@point_of_sale/app/models/product_template_attribute_line';

patch(ProductTemplateAttributeLine.prototype, {
    values() {
        const values = super.values();
        return [...values].sort((left, right) =>
            (left.shop_pos_sequence ?? 0) - (right.shop_pos_sequence ?? 0) || left.id - right.id
        );
    },
});
