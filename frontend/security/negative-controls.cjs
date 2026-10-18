// Explicit original-version control, never part of the remediated install.
// Usage: node security/negative-controls.cjs /absolute/original/node_modules
const assert = require('node:assert/strict');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawnSync } = require('node:child_process');
const root = path.resolve(process.argv[2]);
const braces = path.join(root, 'braces');
const recursion = spawnSync(process.execPath, ['-e', `
  const assert = require('node:assert/strict');
  const braces = require(${JSON.stringify(braces)});
  assert.throws(() => braces.compile('{'.repeat(4500) + 'a,b' + '}'.repeat(4500)), RangeError);
`], { encoding: 'utf8', timeout: 5000 });
assert.equal(recursion.error, undefined); assert.equal(recursion.status, 0, recursion.stderr);
assert.equal(require(path.join(root, 'braces/package.json')).version, '3.0.3');
const forge = require(path.join(root, 'node-forge'));
assert.equal(require(path.join(root, 'node-forge/package.json')).version, '1.4.0');
const pair = crypto.generateKeyPairSync('rsa', { modulusLength: 2048, publicExponent: 3 });
const message = Buffer.from('Synthetic signature validation control');
const digest = crypto.createHash('sha256').update(message).digest();
const info = Buffer.concat([Buffer.from('3033300f0609608648016503040201050005000420', 'hex'), digest]);
const em = Buffer.concat([Buffer.from([0, 1]), Buffer.alloc(256 - info.length - 3, 255), Buffer.from([0]), info]);
const signature = crypto.privateEncrypt({ key: pair.privateKey, padding: crypto.constants.RSA_NO_PADDING }, em);
const publicKey = forge.pki.publicKeyFromPem(pair.publicKey.export({ format: 'pem', type: 'spki' }));
assert.equal(publicKey.verify(digest.toString('binary'), signature.toString('binary')), true);
assert.equal(crypto.verify('sha256', message, pair.publicKey, signature), false);
console.log(JSON.stringify({ braces: { version: '3.0.3', nestedPatternStackExhaustion: true },
  forge: { version: '1.4.0', extraDigestAlgorithmChildAccepted: true, nodeRejectsSameSignature: true } }, null, 2));
