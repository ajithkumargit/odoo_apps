/** @odoo-module **/

import { PosStore } from '@point_of_sale/app/services/pos_store';
import { patch } from '@web/core/utils/patch';

patch(PosStore.prototype, {
    async setup(...args) {
        await super.setup(...args);
        // Core defines this handler as an instance function.
        const configureProduct = this.handleConfigurableProduct;
        this.handleConfigurableProduct = async (values, template, opts = {}, configure = true) => {
            const result = await configureProduct(values, template, opts, configure);
            const product = values.product_id;
            if (result !== false && product?.shop_loose_weight &&
                product.shop_variant_weight_grams > 0 && !opts.shopExplicitWeightQuantity &&
                opts.code?.type !== 'weight') {
                values.qty *= product.shop_variant_weight_grams / 1000;
            }
            // Electronic-scale measurements run after this handler and replace qty.
            return result;
        };
    },

    async addLineToOrder(vals, order, opts = {}, configure = true) {
        return super.addLineToOrder(vals, order, {
            ...opts,
            // Explicit quantities (including refunds and measured weights) are kg already.
            shopExplicitWeightQuantity: vals.qty !== undefined,
        }, configure);
    },
});
