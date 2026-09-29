// Run with node. Exercises the actual POS extension against the core handler contract.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');

class PosStore {
    async setup() {
        this.handleConfigurableProduct = async (values, template, opts) => {
            if (opts.cancel) return false;
            values.product_id = template.variant;
            if (opts.count) values.qty = opts.count;
            return true;
        };
    }
    async addLineToOrder(vals, order, opts, configure) {
        const values = { qty: 1, ...vals };
        const result = await this.handleConfigurableProduct(values, vals.product_tmpl_id, opts, configure);
        return result === false ? false : values.qty;
    }
}
function patch(target, extension) {
    const previous = Object.create(Object.getPrototypeOf(target), Object.getOwnPropertyDescriptors(target));
    Object.setPrototypeOf(extension, previous);
    Object.defineProperties(target, Object.getOwnPropertyDescriptors(extension));
}
const code = fs.readFileSync(path.join(__dirname, '../static/src/js/pos_weight_quantity.js'), 'utf8')
    .replace(/^import .*;\r?\n/gm, '');
vm.runInNewContext(code, { PosStore, patch });

(async () => {
    const store = new PosStore();
    await store.setup();
    const template = { variant: { shop_loose_weight: true, shop_variant_weight_grams: 250 } };
    const add = (vals = {}, opts = {}) => store.addLineToOrder({ product_tmpl_id: template, ...vals }, {}, opts);
    assert.equal(await add(), 0.25);
    assert.equal(await add({}, { count: 3 }), 0.75);
    assert.equal(await add({ qty: -0.25 }), -0.25);
    assert.equal(await add({ qty: 0.7 }), 0.7);
    assert.equal(await add({ qty: 0 }), 0);
    assert.equal(await add({}, { code: { type: 'weight' } }), 1);
    assert.equal(await add({}, { cancel: true }), false);
    template.variant.shop_loose_weight = false;
    assert.equal(await add(), 1);
    console.log('POS weight quantity: 8 assertions passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
