const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const test = require('node:test');

// Expo SDK 57 keeps RN 0.86 / Metro 0.84.4. Narrow call-site patches bridge
// patched dependencies without downgrading Expo or forking either parser.
test('patched Metro handles buffer and filesystem assets on web/iOS/Android', async () => {
  const { getAssetData, getAssetSize } = require('metro/private/Assets');
  const file = path.resolve('assets/images/icon.png');
  assert.deepEqual(getAssetSize('png', fs.readFileSync(file), file), { width: 1024, height: 1024 });
  for (const platform of ['web', 'ios', 'android']) {
    const asset = await getAssetData(file, 'assets/images/icon.png', [], platform, '/assets');
    assert.equal(asset.width, 1024);
    assert.equal(asset.height, 1024);
    assert.equal(asset.type, 'png');
  }
  const lock = JSON.parse(fs.readFileSync('package-lock.json'));
  assert.equal(lock.packages['node_modules/metro'].version, '0.84.4');
  assert.equal(lock.packages['node_modules/query-string'].version, '7.1.3');
  assert.equal(lock.packages['node_modules/image-size'].version, '2.0.4');
  assert.equal(lock.packages['node_modules/decode-uri-component'].version, '0.5.0');
});

test('Expo Router query parser retains Unicode, duplicate and malformed-link behavior', () => {
  const query = require('query-string');
  assert.deepEqual({ ...query.parse('name=caf%C3%A9&item=x%3Ay&item=z&empty=') },
    { name: 'café', item: ['x:y', 'z'], empty: '' });
  assert.equal(query.parse('q=%ZZ').q, '%ZZ');
  assert.equal(query.parse(query.stringify({ name: '東京', route: '/attention' })).name, '東京');
});

test('hostile deep links and image headers terminate in bounded subprocesses', () => {
  const scripts = [
    `const q=require('query-string'); q.parse('value='+('%FF'.repeat(10000)));`,
    ...[
      '69636e73000000106963303700000000', // ICNS zero-length entry
      '00000000667479706865696300000000', // HEIF zero-length box
      '0000000c4a584c200d0a870a00000000667479706a786c20', // JXL zero-length ftyp
    ].map(hex => `const assert=require('node:assert/strict');
      const {getAssetSize}=require('metro/private/Assets');
      assert.throws(()=>getAssetSize('png',Buffer.from('${hex}','hex'),'hostile.png'));`),
  ];
  for (const script of scripts) {
    const result = spawnSync(process.execPath, ['-e', script], { timeout: 3000, encoding: 'utf8' });
    assert.equal(result.error, undefined, 'Dependency parser timed out');
    assert.equal(result.status, 0, result.stderr);
  }
});
