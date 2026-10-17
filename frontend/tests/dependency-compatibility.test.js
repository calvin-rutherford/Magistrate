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

const watcherIncluded = () => ({
  extensions: new Set(['js', 'd.ts', 'env']),
  basenames: new Set(['package.json']),
  basenamePrefixes: ['.metro-health-check-'],
});

test('Expo file-map removes only its micromatch edge and preserves filename matching', () => {
  const lock = JSON.parse(fs.readFileSync('package-lock.json'));
  const locked = lock.packages['node_modules/@expo/metro-file-map'];
  const installed = require('@expo/metro-file-map/package.json');
  assert.equal(locked.version, '57.0.4');
  assert.equal(installed.version, locked.version);
  assert.equal(Object.hasOwn(locked.dependencies, 'micromatch'), false);
  assert.equal(Object.hasOwn(installed.dependencies, 'micromatch'), false);
  const { isIncluded } = require('@expo/metro-file-map/build/watchers/common');
  const included = watcherIncluded();
  const cases = [
    ['app.js', true], ['src/app.js', true], ['app.jsx', false],
    ['app.JS', false], ['app.js.map', false], ['app..js', true],
    ['.env', true], ['src/.env', true], ['.env.local', false],
    ['.envrc', false], ['.js', true], ['.hidden/app.js', true],
    ['types.d.ts', true], ['src/types.d.ts', true], ['types.ts', false],
    ['types.d.ts.map', false], ['.d.ts', true], ['types.d.tsx', false],
    ['package.json', true], ['nested/package.json', true], ['not-package.json', false],
    ['package.json.bak', false], ['.metro-health-check-123', true],
    ['nested/.metro-health-check-123', true], ['.metro-health-check-', true],
    ['x.metro-health-check-123', false], ['.metro-health-check', false],
  ];
  for (const [name, expected] of cases) {
    assert.equal(isIncluded('f', included, name.split('/').join(path.sep)), expected, name);
  }
  assert.equal(isIncluded('f', null, '.arbitrary'), true);
  assert.equal(isIncluded('d', included, '.directory'), true);
  assert.equal(isIncluded('l', included, 'linked-file'), true);
});

test('Expo fallback watcher observes real add/change/delete for matched filenames', { timeout: 15000 }, async () => {
  const FallbackWatcher = require('@expo/metro-file-map/build/watchers/FallbackWatcher').default;
  // Synthetic files stay in the ignored worktree, never an operator env directory.
  fs.mkdirSync('.expo', { recursive: true });
  const root = fs.mkdtempSync(path.resolve('.expo/watcher-compat-'));
  fs.mkdirSync(path.join(root, 'src'));
  const watcher = new FallbackWatcher(root, {
    included: watcherIncluded(), ignored: null, watchmanDeferStates: [],
  });
  const errors = [];
  watcher.onError(error => errors.push(error));
  async function bounded(promise) {
    let timer;
    try {
      return await Promise.race([promise, new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error('Watcher operation timed out')), 3000);
      })]);
    } finally {
      clearTimeout(timer);
    }
  }
  async function change(name, event, action) {
    let removeEvent;
    let removeError;
    const received = new Promise((resolve, reject) => {
      removeEvent = watcher.onFileEvent(change => {
        if (change.relativePath === name && change.event === event) resolve(change);
      });
      removeError = watcher.onError(reject);
    });
    try {
      action();
      return await bounded(received);
    } finally {
      removeEvent();
      removeError();
    }
  }
  try {
    await bounded(watcher.startWatching());
    for (const name of ['.env', 'types.d.ts', path.join('src', 'app.js'), 'package.json', '.metro-health-check-test']) {
      const file = path.join(root, name);
      const added = await change(name, 'touch', () => fs.writeFileSync(file, 'first'));
      assert.equal(added.metadata.type, 'f');
      assert.equal(added.metadata.size, 5);
      const changed = await change(name, 'touch', () => fs.appendFileSync(file, '-second'));
      assert.equal(changed.metadata.size, 12);
      await change(name, 'delete', () => fs.unlinkSync(file));
    }
    assert.deepEqual(errors, []);
  } finally {
    await watcher.stopWatching();
    fs.rmSync(root, { recursive: true, force: true });
  }
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

test('shell quoting rejects line terminators after comment tokens', () => {
  const { quote, parse } = require('shell-quote');
  const lock = JSON.parse(fs.readFileSync('package-lock.json'));
  assert.equal(lock.packages['node_modules/shell-quote'].version, '1.12.0');
  assert.deepEqual(parse(quote(['tool', 'space value', 'café'])), ['tool', 'space value', 'café']);
  for (const terminator of ['\n', '\r', '\u2028', '\u2029']) {
    assert.throws(() => quote(['echo', 'ok', { comment: 'note' }, `arg${terminator}untrusted;#`]), TypeError);
  }
});

test('indexed source maps reject excessive offsets in a bounded subprocess', () => {
  const lock = JSON.parse(fs.readFileSync('package-lock.json'));
  assert.equal(lock.packages['node_modules/source-map-js'].version, '1.2.2');
  const result = spawnSync(process.execPath, ['-e', `
    const assert = require('node:assert/strict');
    const { SourceMapConsumer } = require('source-map-js');
    const map = { version: 3, sources: ['source.js'], names: [], mappings: 'AAAA' };
    const indexed = line => ({ version: 3, sections: [{ offset: { line, column: 0 }, map }] });
    const mappings = [];
    new SourceMapConsumer(indexed(1)).eachMapping(mapping => mappings.push(mapping));
    assert.equal(mappings[0].generatedLine, 2);
    assert.equal(mappings[0].originalLine, 1);
    for (const line of [1e12, Infinity, -1, 1.5]) {
      assert.throws(() => new SourceMapConsumer(indexed(line)), /Section offset/);
    }
  `], { timeout: 3000, encoding: 'utf8' });
  assert.equal(result.error, undefined, 'Source-map offset validation timed out');
  assert.equal(result.status, 0, result.stderr);
});
