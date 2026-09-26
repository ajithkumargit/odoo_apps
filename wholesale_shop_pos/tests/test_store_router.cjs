// Run with node custom_addons/wholesale_shop_pos/tests/test_store_router.cjs
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
function source(path) {
    return fs.readFileSync(path, 'utf8').replace(/^import .*;\r?\n/gm, '').replace(/^export /gm, '');
}
const context = vm.createContext({
    URL, console,
    EventBus: class { trigger() {} },
    omit: (obj, ...keys) => Object.fromEntries(Object.entries(obj).filter(([k]) => !keys.includes(k))),
    pick: (obj, ...keys) => Object.fromEntries(Object.entries(obj).filter(([k]) => keys.includes(k))),
    objectToUrlEncodedString: obj => new URLSearchParams(obj).toString(),
    compareUrls: (a, b) => a === b,
    isDisplayStandalone: () => false,
    isNumeric: value => /^\d+$/.test(String(value)),
    slidingWindow: (values, size) => values.slice(size-1).map((_, i) => values.slice(i, i+size)),
    browser: { location: new URL('http://localhost:8070/store/purchase/6?debug=1'),
        addEventListener() {}, history: { replaceState() {} }, clearTimeout() {}, setTimeout() {} },
});
vm.runInContext(source('addons/web/static/src/core/browser/router.js'), context);
vm.runInContext(source('addons/web/static/src/core/utils/patch.js'), context);
vm.runInContext(source('custom_addons/wholesale_shop_pos/static/src/js/store_router.js'), context);
assert.equal(vm.runInContext('router.current.resId', context), 6);
assert.equal(vm.runInContext('router.current.action', context), 'purchase');
for (const path of ['/purchase/6?debug=1', '/action-123/22', '/m-product.product/9', '/purchase/new', '']) {
    context.testPath = path;
    assert.equal(vm.runInContext('JSON.stringify(router.urlToState(new URL("http://localhost/odoo" + testPath)))', context),
        vm.runInContext('JSON.stringify(router.urlToState(new URL("http://localhost/store" + testPath)))', context));
}
assert.equal(vm.runInContext('router.stateToUrl({action: "purchase", resId: 6})', context), '/store/purchase/6');
assert.equal(vm.runInContext('router.stateToUrl(router.current)', context), '/store/purchase/6?debug=1');
assert.equal(vm.runInContext('router.urlToState(new URL("http://localhost/web#model=product.product&id=9")).resId', context), 9);
console.log('Store router: deep links, query strings, legacy URLs and generated URLs passed');
