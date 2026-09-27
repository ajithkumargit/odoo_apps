const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
let drawArgs, opened, updated;
const canvas = { getContext: () => ({ drawImage: (...args) => { drawArgs = args; } }), toDataURL: () => 'data:image/png;base64,YQ==' };
const ctx = vm.createContext({ Component: class {}, CharField: class {}, charField: {}, Dialog: class {},
    registry: { category: () => ({ add() {} }) }, _t: s => s, isBinarySize: s => s === '10 KB',
    document: { createElement: () => canvas } });
vm.runInContext(fs.readFileSync('custom_addons/wholesale_shop_pos/static/src/js/line_name_crop.js', 'utf8').replace(/^import .*;\r?\n/gm, '').replace(/^export /gm, ''), ctx);
const DialogClass = vm.runInContext('LineNameCropDialog', ctx), Field = vm.runInContext('LineNameCropField', ctx);
(async () => {
    const d = Object.create(DialogClass.prototype);
    d.state = { ready: true, busy: false };
    d.image = { el: { naturalWidth: 2000, naturalHeight: 1000, getBoundingClientRect: () => ({ left: 0, top: 0, width: 200, height: 100 }) } };
    d.start({ clientX: 150, clientY: 80, button: 0, preventDefault() {}, currentTarget: { setPointerCapture() {} } });
    d.move({ clientX: 50, clientY: 20 });
    d.end();
    assert.equal(d.state.crop.x, 0.25);
    assert.equal(d.state.crop.w, 0.5);
    d.props = { importId: 22 };
    d.orm = { call: async () => ({ text: 'Tea 250g' }) };
    await d.extract();
    assert.deepEqual(drawArgs.slice(1, 4), [500, 200, 1000]);
    assert.equal(d.state.text, 'Tea 250g');
    const f = Object.create(Field.prototype);
    f.props = { name: 'raw_description', record: { model: { root: { resModel: 'shop.purchase.import', resId: 22,
        data: { state: 'review', original_is_image: true, original_file: 'iNEWIMAGE', original_file_name: 'changed.png' } } },
        data: {}, update: async values => { updated = values; } } };
    f.dialog = { add: (_, props) => { opened = props; } };
    f.crop();
    assert.equal(opened.localSources[0].key, 'shop.purchase.import:22');
    assert.equal(opened.localSources[0].url, 'data:image/png;base64,iNEWIMAGE');
    await opened.onApply('Tea 250g');
    assert.equal(JSON.stringify(updated), JSON.stringify({ raw_description: 'Tea 250g' }));
    f.props.record.model.root.data.state = 'po_created';
    assert.equal(f.canCrop, false);
    console.log('Passed: reverse drag, full-resolution crop, unsaved image, description-only update, locked bill.');
})().catch(error => { console.error(error); process.exitCode = 1; });
