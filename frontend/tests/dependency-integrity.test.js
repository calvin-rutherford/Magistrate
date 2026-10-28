const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const test = require('node:test');
const root = path.resolve(__dirname, '..');
const verifier = path.join(root, 'security/verify-install.cjs');
const lock = require('../package-lock.json');
const artifacts = require('../security/artifacts.json');
function temporary() { fs.mkdirSync(path.join(root, '.expo'), { recursive: true }); return fs.mkdtempSync(path.join(root, '.expo/integrity-')); }

test('install guard fails closed on drift, extra bytes, reintroduced edges and stale bundled copies', () => {
  const dir = temporary();
  try {
    fs.writeFileSync(path.join(dir, 'package-lock.json'), JSON.stringify(lock));
    for (const artifact of artifacts) {
      for (const [location, value] of Object.entries(lock.packages)) {
        if (!location.endsWith('node_modules/' + artifact.name) || value.version !== artifact.version) continue;
        for (const file of Object.keys(artifact.files)) {
          const target = path.join(dir, location, file);
          fs.mkdirSync(path.dirname(target), { recursive: true });
          fs.copyFileSync(path.join(root, location, file), target);
        }
      }
    }
    const verify = () => spawnSync(process.execPath, [verifier, dir], { encoding: 'utf8', timeout: 15000 });
    assert.equal(verify().status, 0);
    const changed = path.join(dir, 'node_modules/metro-file-map/src/watchers/common.js');
    const original = fs.readFileSync(changed);
    fs.appendFileSync(changed, '\n// unauthorized change');
    assert.match(verify().stderr, /Consumer drift/);
    fs.writeFileSync(changed, original);
    const extra = path.join(path.dirname(changed), 'unreviewed.js');
    fs.writeFileSync(extra, 'module.exports = true;');
    assert.match(verify().stderr, /Unexpected consumer bytes/);
    fs.unlinkSync(extra);
    const stale = path.join(dir, 'node_modules/hidden-bundle.js');
    fs.writeFileSync(stale, 'braces.compile = function () {};');
    assert.match(verify().stderr, /Stale affected/);
    fs.unlinkSync(stale);
    const injected = structuredClone(lock);
    injected.packages['node_modules/foreign'] = { version: '1.0.0', dependencies: { 'node-forge': '1.4.0' } };
    fs.writeFileSync(path.join(dir, 'package-lock.json'), JSON.stringify(injected));
    assert.match(verify().stderr, /Affected dependency edge/);
    fs.writeFileSync(path.join(dir, 'package-lock.json'), JSON.stringify(lock));
    assert.equal(verify().status, 0);
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});

test('patch-package remains mandatory and exits nonzero for a stale compatibility patch', () => {
  assert.equal(require('../package.json').scripts.postinstall, 'node security/verify-install.cjs && patch-package --error-on-fail');
  const dir = temporary();
  try {
    fs.mkdirSync(path.join(dir, 'node_modules/metro/src'), { recursive: true });
    fs.mkdirSync(path.join(dir, 'patches'));
    fs.writeFileSync(path.join(dir, 'package.json'), JSON.stringify({ name: 'patch-failure-fixture', version: '1.0.0' }));
    fs.writeFileSync(path.join(dir, 'package-lock.json'), JSON.stringify({ name: 'patch-failure-fixture', lockfileVersion: 3, packages: {} }));
    fs.copyFileSync(require.resolve('metro/package.json'), path.join(dir, 'node_modules/metro/package.json'));
    const file = path.join(dir, 'node_modules/metro/src/Assets.js');
    const content = '// Deliberately incompatible consumer: the patch MUST fail.\n';
    fs.writeFileSync(file, content);
    fs.copyFileSync(path.join(root, 'patches/metro+0.84.4.patch'), path.join(dir, 'patches/metro+0.84.4.patch'));
    const result = spawnSync(process.execPath, [require.resolve('patch-package/index.js'), '--error-on-fail'], {
      cwd: dir, encoding: 'utf8', timeout: 15000,
    });
    assert.equal(result.error, undefined);
    assert.notEqual(result.status, 0);
    assert.match(result.stdout + result.stderr, /Failed to apply patch/);
    assert.equal(fs.readFileSync(file, 'utf8'), content);
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});
