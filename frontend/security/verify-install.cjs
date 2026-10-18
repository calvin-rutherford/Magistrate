// Verify full source-derived consumers, not package-name/version camouflage.
// Runs before patch-package in every npm ci; errors deliberately terminate install.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const root = path.resolve(process.argv[2] || path.join(__dirname, '..'));
const artifacts = JSON.parse(fs.readFileSync(path.join(__dirname, 'artifacts.json')));
const lock = JSON.parse(fs.readFileSync(path.join(root, 'package-lock.json')));
const sha = value => crypto.createHash('sha256').update(value).digest('hex');
for (const artifact of artifacts) {
  const packed = fs.readFileSync(path.join(__dirname, 'artifacts', artifact.artifact));
  assert.equal(sha(packed), artifact.sha256, 'Vendored archive digest mismatch');
  const locations = Object.entries(lock.packages).filter(([key, value]) =>
    key.endsWith('node_modules/' + artifact.name) && value.version === artifact.version);
  assert(locations.length > 0, `Missing consumer ${artifact.key}`);
  for (const [location, locked] of locations) {
    assert.equal(locked.integrity, artifact.integrity, `Unreviewed resolution for ${location}`);
    assert.equal(locked.resolved, `file:security/artifacts/${artifact.artifact}`, `Unreviewed source for ${location}`);
    const installed = path.join(root, location);
    const files = [];
    function inventory(dir, prefix = '') {
      assert(fs.lstatSync(dir).isDirectory(), `Nonregular consumer directory: ${dir}`);
      for (const item of fs.readdirSync(dir, { withFileTypes: true })) {
        if (!prefix && item.name === 'node_modules') continue; // separately locked dependencies
        const relative = prefix + item.name;
        assert(!item.isSymbolicLink(), `Symlink in consumer: ${relative}`);
        if (item.isDirectory()) inventory(path.join(dir, item.name), relative + '/');
        else { assert(item.isFile(), `Nonregular consumer file: ${relative}`); files.push(relative); }
      }
    }
    inventory(installed);
    assert.deepEqual(files.sort(), Object.keys(artifact.files).sort(), `Unexpected consumer bytes: ${location}`);
    for (const [file, expected] of Object.entries(artifact.files)) {
      const full = path.join(root, location, file);
      assert(fs.lstatSync(full).isFile(), `Nonregular source: ${location}/${file}`);
      assert.equal(sha(fs.readFileSync(full)), expected, `Consumer drift: ${location}/${file}`);
    }
  }
}
const removed = new Set(['braces', 'micromatch', 'node-forge']);
for (const [location, value] of Object.entries(lock.packages)) {
  assert(![...removed].some(name => location.endsWith('node_modules/' + name)), `Affected package remains: ${location}`);
  for (const name of Object.keys(value.dependencies || {})) assert(!removed.has(name), `Affected dependency edge remains: ${location} -> ${name}`);
}
// Check installed bytes, including nested/bundled dependencies not in the lock.
// These unique original implementation markers cover stale Forge/braces copies;
// all JS and source maps are inspected, including build and dist directories.
function scan(dir) {
  for (const item of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, item.name);
    if (item.isSymbolicLink()) continue; // npm's bin links have already-scanned targets
    if (item.isDirectory()) {
      assert(!removed.has(item.name), `Affected directory remains: ${full}`);
      scan(full);
    } else if (/\.(?:[cm]?js|map|json)$/.test(item.name)) {
      const content = fs.readFileSync(full, 'utf8');
      assert(!/function _decodePkcs1_v1_5\(|_parseAllDigestBytes|braces\.compile\s*=|require\(["'](?:node-forge|micromatch|braces)["']\)/.test(content), `Stale affected implementation/consumer: ${full}`);
    }
  }
}
scan(path.join(root, 'node_modules'));
console.log('Verified content-addressed consumers; affected packages, edges and bundled implementation markers absent.');
