const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const ctx = vm.createContext({ Component: class {}, standardFieldProps: {}, registry: { category: () => ({ add() {} }) },
    isBinarySize: value => /^\d+ KB$/.test(value), imageUrl: (model,id,field,opts) => `${model}/${id}/${field}?unique=${opts.unique}` });
vm.runInContext(fs.readFileSync('custom_addons/wholesale_shop_pos/static/src/js/bill_crop_field.js','utf8').replace(/^import .*;\r?\n/gm,'').replace(/^export /gm,''),ctx);
const Field = vm.runInContext('BillCropField',ctx), f = Object.create(Field.prototype);
f.cacheKey = 123;
f.props = { name: 'original_file', record: { resModel:'shop.purchase.import', resId:22, data:{original_file:'10 KB',write_date:'2026-09-26 10:00:00'} } };
const first = f.url;
f.props.record.data.write_date = '2026-09-26 10:01:00';
assert.notEqual(f.url,first);
f.props.record.data.original_file = 'iNEWPNGDATA';
assert.equal(f.url,'data:image/png;base64,iNEWPNGDATA');
f.props.record.data.original_file = false;
assert.equal(f.url,'/web/static/img/placeholder.png');
console.log('Crop preview checks passed: same-size replacement, unsaved image, removed image');
