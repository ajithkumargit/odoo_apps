const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const context = vm.createContext({
    Component: class {}, CharField: class {}, charField: {}, Dialog: class {},
    BarcodeVideoScanner: class {}, registry: { category: () => ({ add() {} }) },
});
const source = fs.readFileSync('custom_addons/wholesale_shop_pos/static/src/js/product_barcode_field.js', 'utf8')
    .replace(/^import .*;\r?\n/gm, '').replace(/^export /gm, '');
vm.runInContext(source, context);
(async () => {
    const Field = vm.runInContext('ProductBarcodeField', context);
    let dialogProps, stored;
    const field = Object.create(Field.prototype);
    field.props = { name: 'barcode', readonly: false, record: { update: async values => { stored = values; } } };
    field.dialog = { add: (_, props) => { dialogProps = props; } };
    field.scan();
    await dialogProps.onResult('0012345678905');
    assert.equal(stored.barcode, '0012345678905');
    stored = undefined;
    field.props.readonly = true;
    await dialogProps.onResult('999');
    assert.equal(stored, undefined);
    field.props.readonly = false;
    field.destroyed = true;
    await dialogProps.onResult('999');
    assert.equal(stored, undefined);
    const Scanner = vm.runInContext('ProductBarcodeDialog', context);
    const scanner = Object.create(Scanner.prototype);
    let writes = 0, closes = 0;
    scanner.props = { onResult: async () => { writes++; }, close: () => { closes++; } };
    await Promise.all([scanner.onResult('00123'), scanner.onResult('00123')]);
    assert.equal(writes, 1);
    assert.equal(closes, 1);
    console.log('Barcode field checks passed: leading zeros, readonly, navigation cleanup, duplicate scan protection');
})().catch(error => { console.error(error); process.exitCode = 1; });
