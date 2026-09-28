const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const services = new Map(), menu = new Map([['install_pwa', {}], ['odoo_account', {}], ['preferences', {}]]);
const events = {};
const state = { isAvailable: true, canPromptToInstall: true, show() { throw Error('Install must be disabled'); }, startUrl: '/odoo' };
services.set('pwa', { start: () => state });
const ctx = vm.createContext({ document: { title: 'Odoo' }, window: { addEventListener: (name, fn) => { events[name] = fn; } },
    _t: s => s, ErrorDialog: class {}, ClientErrorDialog: class {}, NetworkErrorDialog: class {},
    RPCErrorDialog: class { inferTitle() { this.title = 'Odoo Server Error'; } }, WarningDialog: class { inferTitle() { return 'Odoo Warning'; } },
    registry: { category: name => ({ get: key => services.get(key), add: (key, value) => services.set(key, value), remove: key => menu.delete(key) }) } });
const run = path => vm.runInContext(fs.readFileSync(path, 'utf8').replace(/^import .*;\r?\n/gm, '').replace(/^export /gm, ''), ctx);
run('addons/web/static/src/core/utils/patch.js');
run('addons/web/static/src/core/browser/title_service.js');
run('custom_addons/wholesale_shop_pos/static/src/js/store_branding.js');
const title = services.get('title').start();
assert.equal(title.current, 'Wholesale Shop');
title.setCounters({ chat: 3 });
assert.equal(title.current, '(3) Wholesale Shop');
title.setParts({ action: 'Purchase Bills' });
assert.equal(title.current, '(3) Purchase Bills');
title.setParts({ action: null });
assert.equal(title.current, '(3) Wholesale Shop');
const pwa = services.get('pwa').start();
pwa.isAvailable = true; pwa.canPromptToInstall = true;
assert.equal(pwa.isAvailable, false); assert.equal(pwa.canPromptToInstall, false);
pwa.show();
let prevented = false;
events.beforeinstallprompt({ preventDefault: () => { prevented = true; } });
assert.equal(prevented, true);
assert.equal(menu.has('install_pwa'), false); assert.equal(menu.has('odoo_account'), false);
assert.equal(menu.has('preferences'), true);
console.log('Passed: title fallback, counters, action titles, suppressed install event and prompt, preserved preferences.');
