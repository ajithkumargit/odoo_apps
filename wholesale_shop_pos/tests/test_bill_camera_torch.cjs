// Run: node custom_addons/wholesale_shop_pos/tests/test_bill_camera_torch.cjs
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const context = vm.createContext({
    Component: class {}, BinaryField: class {}, binaryField: {}, Dialog: class {},
    registry: { category: () => ({ add() {} }) }, _t: text => text,
});
vm.runInContext(fs.readFileSync('custom_addons/wholesale_shop_pos/static/src/js/bill_camera_field.js', 'utf8')
    .replace(/^import .*;\r?\n/gm, '').replace(/^export /gm, ''), context);
const Camera = vm.runInContext('BillCameraDialog', context);
const camera = () => {
    const value = Object.create(Camera.prototype);
    value.state = { ready: true, saving: false, torchAvailable: true, torchOn: false, torchBusy: false, torchError: '' };
    return value;
};
(async () => {
    const instance = camera();
    const changes = [];
    instance.videoTrack = { applyConstraints: async constraints => changes.push(constraints.advanced[0].torch) };
    await instance.toggleTorch();
    assert.equal(instance.state.torchOn, true);
    await instance.toggleTorch();
    assert.equal(instance.state.torchOn, false);
    assert.deepEqual(changes, [true, false]);
    instance.videoTrack.applyConstraints = async () => { throw new Error('Unsupported'); };
    await instance.toggleTorch();
    assert.equal(instance.state.torchOn, false);
    assert.equal(instance.state.torchBusy, false);
    assert.ok(instance.state.torchError);
    instance.state.torchAvailable = false;
    instance.state.torchError = '';
    await instance.toggleTorch();
    assert.equal(instance.state.torchError, '');
    const pending = camera();
    let finish, calls = 0;
    pending.videoTrack = { applyConstraints: () => { calls++; return new Promise(resolve => { finish = resolve; }); } };
    const toggle = pending.toggleTorch();
    await pending.toggleTorch();
    assert.equal(calls, 1);
    let stopped = 0;
    pending.stream = { getTracks: () => [{ stop: () => { stopped++; } }] };
    pending.destroyed = true;
    pending.stopCamera();
    finish();
    await toggle;
    assert.equal(stopped, 1);
    assert.equal(pending.videoTrack, null);
    assert.equal(pending.state.torchOn, false);
    console.log('Flashlight checks passed: on/off, unsupported device, rejected constraint, repeated clicks, camera cleanup');
})().catch(error => { console.error(error); process.exitCode = 1; });
