// Execute the original package's bundled CommonJS module factory in isolation.
// No CLI/session/provider startup, source mutation, network or credentials.
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(process.argv[2]);
const chunks = path.join(root, 'dist/bundle/chunks');
let found = 0;
for (const file of fs.readdirSync(chunks).filter(file => file.endsWith('.js'))) {
  const bytes = fs.readFileSync(path.join(chunks, file));
  const source = bytes.toString();
  const marker = 'var require_subresource_integrity=__commonJS({"node_modules/undici/lib/web/subresource-integrity/subresource-integrity.js"';
  const start = source.indexOf(marker);
  if (start < 0) continue;
  const end = source.indexOf('var require_', start + marker.length);
  assert(end > start);
  const factory = source.slice(start, end);
  const context = {
    __commonJS(callbacks) {
      let module;
      return () => {
        if (!module) {
          module = { exports: {} };
          Object.values(callbacks)[0](module.exports, module);
        }
        return module.exports;
      };
    },
    __require: require,
    require_runtime_features: () => ({ runtimeFeatures: new Set(['crypto']) }),
  };
  const integrity = vm.runInNewContext(factory + ';require_subresource_integrity()', context, { timeout: 1000 });
  const body = Buffer.from('synthetic bundled integrity control');
  const correct = crypto.createHash('sha256').update(body).digest('base64');
  const wrong = Buffer.alloc(32).toString('base64');
  const results = {
    package: JSON.parse(fs.readFileSync(path.join(root, 'package.json'))).version,
    chunk: file,
    chunk_sha256: crypto.createHash('sha256').update(bytes).digest('hex'),
    factory_sha256: crypto.createHash('sha256').update(factory).digest('hex'),
    valid_lowercase_accepted: integrity.bytesMatch(body, 'sha256-' + correct),
    wrong_lowercase_accepted: integrity.bytesMatch(body, 'sha256-' + wrong),
    wrong_uppercase_accepted: integrity.bytesMatch(body, 'SHA256-' + wrong),
  };
  assert.equal(results.valid_lowercase_accepted, true);
  assert.equal(results.wrong_lowercase_accepted, false);
  console.log(JSON.stringify(results, null, 2));
  found++;
}
assert.equal(found, 1, 'Expected exactly one executable bundled integrity module');
