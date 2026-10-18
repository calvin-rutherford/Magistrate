// The original libraries are exercised separately by security/negative-controls.cjs.
// This suite works both against hash-verified source fixtures and installed artifacts.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { createRequire } = require('node:module');
const { spawnSync } = require('node:child_process');
const { EventEmitter } = require('node:events');
const test = require('node:test');
const { csrPEM, trailingCSRInputs } = require('../security/csr-input-fixtures.cjs');
const fixture = process.env.MAGISTRATE_DEPENDENCY_FIXTURE;
const source = (key, installed) => fixture ? path.join(fixture, key) : path.dirname(require.resolve(installed + '/package.json'));
const maps = [fixture ? path.join(fixture, 'metro-file-map-0.84.4')
  : path.dirname(createRequire(require.resolve('metro/package.json')).resolve('metro-file-map/package.json')), fixture
  ? path.join(fixture, 'metro-file-map-0.84.5')
  : path.dirname(createRequire(require.resolve('@expo/metro/package.json')).resolve('metro-file-map/package.json'))];
assert.deepEqual(maps.map(root => require(path.join(root, 'package.json')).version), ['0.84.4', '0.84.5']);
const helperRoot = source('expo-code-signing-certificates-0.0.6', '@expo/code-signing-certificates');
const cliRoot = fixture ? path.join(fixture, 'expo-cli-57.0.27')
  : path.dirname(createRequire(require.resolve('expo/package.json')).resolve('@expo/cli/package.json'));
const helper = require(path.join(helperRoot, 'build/main.js'));
const included = () => ({ extensions: new Set(['js', 'env', 'd.ts']), basenames: new Set(['package.json']), basenamePrefixes: ['.metro-health-check-'] });
function temporary() { fs.mkdirSync('.expo', { recursive: true }); return fs.mkdtempSync(path.resolve('.expo/dependency-removal-')); }
function openssl(args, input) {
  const result = spawnSync('openssl', args, { input, encoding: 'utf8', timeout: 10000 });
  assert.equal(result.error, undefined); assert.equal(result.status, 0, result.stderr); return result.stdout;
}
function malformedSignature(pair, message) {
  // A third child in DigestAlgorithm is the exact GHSA-86w9-cpqp-85rv defect.
  const digest = crypto.createHash('sha256').update(message).digest();
  const info = Buffer.concat([Buffer.from('3033300f0609608648016503040201050005000420', 'hex'), digest]);
  const bytes = pair.publicKey.asymmetricKeyDetails.modulusLength / 8;
  const em = Buffer.concat([Buffer.from([0, 1]), Buffer.alloc(bytes - info.length - 3, 255), Buffer.from([0]), info]);
  return crypto.privateEncrypt({ key: pair.privateKey, padding: crypto.constants.RSA_NO_PADDING }, em);
}

for (const root of maps) {
  const label = `metro-file-map@${require(path.join(root, 'package.json')).version}`;
  test(`${label}: suffix semantics and no removed edge`, () => {
    const meta = require(path.join(root, 'package.json'));
    assert.equal(meta.dependencies.micromatch, undefined);
    const { isIncluded } = require(path.join(root, 'src/watchers/common'));
    for (const [name, want] of [['.env', true], ['x/.env', true], ['.env.local', false], ['.envrc', false],
      ['a.js', true], ['a.JS', false], ['a.js.map', false], ['.js', true], ['.d.ts', true], ['types.d.ts', true],
      ['types.ts', false], ['package.json', true], ['x/package.json', true], ['xpackage.json', false],
      ['.metro-health-check-x', true], ['x/.metro-health-check-', true], ['x.metro-health-check-', false]]) {
      assert.equal(isIncluded('f', included(), name), want, name);
    }
    for (const kind of ['d', 'l', null]) assert.equal(isIncluded(kind, included(), 'other'), true);
    assert.equal(isIncluded('f', null, 'any'), true);
  });
  test(`${label}: real fallback watcher add/change/delete and native event filtering`, { timeout: 15000 }, async () => {
    const rootDir = temporary();
    const Fallback = require(path.join(root, 'src/watchers/FallbackWatcher')).default;
    const Native = require(path.join(root, 'src/watchers/NativeWatcher')).default;
    const watcher = new Fallback(rootDir, { included: included(), ignored: /ignored/, watchmanDeferStates: [] });
    const native = new Native(rootDir, { included: included(), ignored: /ignored/ });
    const nativeEvents = []; native.onFileEvent(event => nativeEvents.push(event));
    const errors = []; watcher.onError(e => errors.push(e)); native.onError(e => errors.push(e));
    async function change(name, event, action) {
      let dispose; let timer;
      try {
        const received = new Promise((resolve, reject) => {
          dispose = watcher.onFileEvent(value => { if (value.relativePath === name && value.event === event) resolve(value); });
          timer = setTimeout(() => reject(new Error(`No ${event} for ${name}`)), 3000);
        });
        action(); return await received;
      } finally { dispose?.(); clearTimeout(timer); }
    }
    try {
      await watcher.startWatching();
      for (const name of ['.env', 'types.d.ts', 'package.json', '.metro-health-check-test']) {
        const file = path.join(rootDir, name);
        await change(name, 'touch', () => fs.writeFileSync(file, 'first'));
        await native._handleEvent('change', name);
        assert.equal(nativeEvents.at(-1).relativePath, name);
        await change(name, 'touch', () => fs.appendFileSync(file, 'second'));
        await change(name, 'delete', () => fs.unlinkSync(file));
      }
      const count = nativeEvents.length;
      for (const name of ['ignored.js', 'other.ts']) {
        fs.writeFileSync(path.join(rootDir, name), 'no'); await native._handleEvent('change', name);
      }
      assert.equal(nativeEvents.length, count); assert.deepEqual(errors, []);
    } finally { await watcher.stopWatching(); await native.stopWatching(); fs.rmSync(rootDir, { recursive: true, force: true }); }
  });
  test(`${label}: Watchman subscribe and structured event compatibility`, async () => {
    const req = createRequire(path.join(root, 'package.json'));
    const fb = req('fb-watchman'); const Original = fb.Client; let client;
    class FakeClient extends EventEmitter {
      constructor() { super(); client = this; this.commands = []; }
      command(args, done) {
        this.commands.push(args);
        done(null, args[0] === 'watch-project' ? { watch: '/fixture' } : args[0] === 'clock' ? { clock: 'c:1' } : { subscribe: args[2] });
      }
      end() {}
    }
    fb.Client = FakeClient;
    const Watchman = require(path.join(root, 'src/watchers/WatchmanWatcher')).default;
    const watcher = new Watchman('/fixture', { included: included(), ignored: /ignored/, watchmanDeferStates: [] });
    const events = []; watcher.onFileEvent(value => events.push(value));
    try {
      await watcher.startWatching();
      assert.deepEqual(client.commands.map(c => c[0]), ['watch-project', 'clock', 'subscribe']);
      assert.equal(client.commands[2][3].expression, undefined);
      client.emit('subscription', { subscription: watcher.subscriptionName, clock: 'c:2', files:
        ['.env', 'types.d.ts', 'package.json', '.metro-health-check-test', 'unmatched.ts', 'ignored.js']
          .map(name => ({ name, exists: true, type: 'f', size: 1, mtime_ms: 1234 })) });
      assert.equal(events.length, 4);
      client.emit('subscription', { subscription: watcher.subscriptionName, clock: 'c:3', files: [{ name: '.env', exists: false, type: 'f' }] });
      assert.equal(events.at(-1).event, 'delete');
    } finally { await watcher.stopWatching(); fb.Client = Original; }
  });
}

test('workspace finder retains ordered positives, exclusions, re-inclusions, only-negatives and root traversal', () => {
  const root = source('find-yarn-workspace-root-2.0.0', 'find-yarn-workspace-root');
  const matches = require(path.join(root, 'workspace-match'));
  for (const [patterns, want] of [
    [['packages/*'], true], [['packages/*', '!packages/app'], false],
    [['!packages/app', 'packages/*'], true], [['!other/*'], true], [['!packages/*'], false],
    [[], false], [['{packages,apps}/*'], true], [['packages/@(app|other)'], true],
    [['packages/!(other)'], true], [['packages/!(app)'], false], [['!packages/*', 'packages/app', '!x'], true],
  ]) assert.equal(matches('packages/app', patterns), want, patterns.join(','));
  assert.equal(matches('packages/.hidden', ['packages/*']), false);
  assert.equal(matches('packages/.hidden', ['packages/.*']), true);
  assert.throws(() => matches('app', ['{'.repeat(10000) + 'x,y' + '}'.repeat(10000)]), /nesting/);
  const dir = temporary();
  try {
    const child = path.join(dir, 'packages/app'); fs.mkdirSync(child, { recursive: true });
    const find = require(path.join(root, 'index.js'));
    for (const workspaces of [['packages/*'], { packages: ['packages/*'] }]) {
      fs.writeFileSync(path.join(dir, 'package.json'), JSON.stringify({ workspaces }));
      assert.equal(find(child), dir); assert.equal(find(dir), dir);
    }
    fs.writeFileSync(path.join(dir, 'packages/package.json'), JSON.stringify({ workspaces: ['excluded'] }));
    assert.equal(find(child), null); // Never bypass the nearest workspace's exclusion.
    fs.writeFileSync(path.join(dir, 'packages/package.json'), 'malformed');
    assert.throws(() => find(child), SyntaxError);
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});

test('unbundled workspace resolver preserves pnpm/package priority, options and async APIs', async () => {
  const resolver = require(source('resolve-workspace-root-2.0.1', 'resolve-workspace-root'));
  const dir = temporary();
  try {
    const child = path.join(dir, 'packages/app'); fs.mkdirSync(child, { recursive: true });
    fs.writeFileSync(path.join(dir, 'pnpm-workspace.yaml'), 'packages: ["packages/*"]');
    assert.equal(resolver.resolveWorkspaceRoot(child), dir); // no package.json required
    assert.equal(await resolver.resolveWorkspaceRootAsync(child), dir);
    assert.equal(resolver.resolveWorkspaceRoot(child, { pnpmWorkspaces: false }), null);
    fs.writeFileSync(path.join(dir, 'package.json'), JSON.stringify({ workspaces: ['other/*'] }));
    assert.deepEqual(resolver.getWorkspaceGlobs(dir), ['packages/*']);
    assert.deepEqual(await resolver.getWorkspaceGlobsAsync(dir), ['packages/*']);
    assert.deepEqual(resolver.getWorkspaceGlobs(dir, { pnpmWorkspaces: false }), ['other/*']);
    fs.writeFileSync(path.join(dir, 'pnpm-workspace.yaml'), 'not: [valid');
    assert.equal(resolver.resolveWorkspaceRoot(child), null);
    fs.writeFileSync(path.join(dir, 'package.json'), JSON.stringify({ workspaces: { packages: ['packages/*'] } }));
    assert.equal(await resolver.resolveWorkspaceRootAsync(child), dir);
    assert.equal(resolver.resolveWorkspaceRoot(child, { packageWorkspaces: false }), null);
    assert.deepEqual(resolver.getWorkspaceGlobs(dir), ['packages/*']);
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});

test('all signing/PEM APIs, UTF-8 manifest and CSR match independent OpenSSL CLI', async () => {
  const dir = temporary();
  try {
    const pair = helper.generateKeyPair(); const pem = helper.convertKeyPairToPEM(pair);
    assert.equal(pair.publicKey.asymmetricKeyDetails.modulusLength, 2048);
    helper.convertKeyPairPEMToKeyPair(pem);
    const cert = await helper.generateSelfSignedCodeSigningCertificate({ keyPair: pair,
      commonName: 'Magi, + café 测试', validityNotBefore: new Date(Date.now() - 60000), validityNotAfter: new Date(Date.now() + 86400000) });
    const certPEM = helper.convertCertificateToCertificatePEM(cert);
    helper.validateSelfSignedCertificate(helper.convertCertificatePEMToCertificate(certPEM), pair);
    fs.writeFileSync(path.join(dir, 'cert.pem'), certPEM); fs.writeFileSync(path.join(dir, 'public.pem'), pem.publicKeyPEM);
    const details = openssl(['x509', '-in', path.join(dir, 'cert.pem'), '-noout', '-text']);
    assert.match(details, /Digital Signature/); assert.match(details, /Code Signing/); assert.match(details, /sha256WithRSAEncryption/);
    openssl(['verify', '-check_ss_sig', '-CAfile', path.join(dir, 'cert.pem'), path.join(dir, 'cert.pem')]);
    const bytes = Buffer.from('{"message":"café 東京 🌳"}\n'); fs.writeFileSync(path.join(dir, 'manifest'), bytes);
    const signature = helper.signBufferRSASHA256AndVerify(pair.privateKey, cert, bytes);
    fs.writeFileSync(path.join(dir, 'sig'), Buffer.from(signature, 'base64'));
    openssl(['dgst', '-sha256', '-verify', path.join(dir, 'public.pem'), '-signature', path.join(dir, 'sig'), path.join(dir, 'manifest')]);
    const cli = require(path.join(cliRoot, 'build/src/utils/codesigning'));
    assert.equal(cli.signManifestString(bytes.toString(), { privateKey: pem.privateKeyPEM, certificateForPrivateKey: certPEM }), signature);
    fs.writeFileSync(path.join(dir, 'key.pem'), pem.privateKeyPEM, { mode: 0o600 });
    const info = await cli.getCodeSigningInfoAsync({ updates: { codeSigningCertificate: path.join(dir, 'cert.pem'), codeSigningMetadata: { alg: 'rsa-v1_5-sha256', keyid: 'test' } } }, 'keyid="test", alg="rsa-v1_5-sha256"', path.join(dir, 'key.pem'));
    assert.equal(info.keyId, 'test');
    const csr = await helper.generateCSR(pair, 'CSR café 测试');
    const csrPEM = helper.convertCSRToCSRPEM(csr); const restored = helper.convertCSRPEMToCSR(csrPEM);
    assert.equal(await restored.verify(crypto.webcrypto), true);
    fs.writeFileSync(path.join(dir, 'request.pem'), csrPEM);
    openssl(['req', '-in', path.join(dir, 'request.pem'), '-verify', '-noout']);
    // Independently constructed CA exercises development issuance and Expo's custom OID.
    openssl(['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', path.join(dir, 'ca-key.pem'), '-out', path.join(dir, 'ca.pem'), '-days', '60', '-subj', '/CN=Fixture CA', '-addext', 'basicConstraints=critical,CA:TRUE']);
    const ca = helper.convertCertificatePEMToCertificate(fs.readFileSync(path.join(dir, 'ca.pem'), 'utf8'));
    const caKey = helper.convertPrivateKeyPEMToPrivateKey(fs.readFileSync(path.join(dir, 'ca-key.pem'), 'utf8'));
    const issued = await helper.generateDevelopmentCertificateFromCSR(caKey, ca, restored, 'fixture-app', '@fixture/project');
    const issuedPEM = helper.convertCertificateToCertificatePEM(issued);
    fs.writeFileSync(path.join(dir, 'issued.pem'), issuedPEM);
    openssl(['verify', '-CAfile', path.join(dir, 'ca.pem'), path.join(dir, 'issued.pem')]);
    assert.equal(Buffer.from(issued.parsed.getExtension(helper.expoProjectInformationOID).value).toString(), 'fixture-app,@fixture/project');
    assert.equal(issued.parsed.subjectName.getField('CN')[0], 'CSR café 测试');
    assert(issued.validity.notAfter <= ca.validity.notAfter);
    // Exact malformed DigestAlgorithm negative control: original Forge accepts it.
    const lowExponent = crypto.generateKeyPairSync('rsa', { modulusLength: 2048, publicExponent: 3 });
    const malformed = malformedSignature(lowExponent, bytes);
    assert.equal(crypto.verify('sha256', bytes, lowExponent.publicKey, malformed), false);
    fs.writeFileSync(path.join(dir, 'bad-sig'), malformed);
    fs.writeFileSync(path.join(dir, 'low.pem'), lowExponent.publicKey.export({ format: 'pem', type: 'spki' }));
    const oracle = spawnSync('openssl', ['dgst', '-sha256', '-verify', path.join(dir, 'low.pem'), '-signature', path.join(dir, 'bad-sig'), path.join(dir, 'manifest')], { timeout: 5000 });
    assert.equal(oracle.status, 1);
    // Reject a tampered CSR signature before any certificate is issued.
    const badCSR = Buffer.from(csr.rawData); badCSR[badCSR.length - 1] ^= 1;
    const badPEM = `-----BEGIN CERTIFICATE REQUEST-----\n${badCSR.toString('base64')}\n-----END CERTIFICATE REQUEST-----`;
    await assert.rejects(helper.generateDevelopmentCertificateFromCSR(caKey, ca, helper.convertCSRPEMToCSR(badPEM), 'app', 'scope'), /CSR not self-signed/);
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});

test('CSR parser consumes the whole input: reject trailing bytes after generated and OpenSSL requests', async () => {
  const dir = temporary();
  try {
    const pair = helper.generateKeyPair();
    const generated = await helper.generateCSR(pair, 'Whole input café');
    fs.writeFileSync(path.join(dir, 'key.pem'), helper.convertKeyPairToPEM(pair).privateKeyPEM, { mode: 0o600 });
    openssl(['req', '-new', '-key', path.join(dir, 'key.pem'), '-outform', 'DER', '-out', path.join(dir, 'request.der'), '-subj', '/CN=Independent CSR framing']);
    for (const der of [Buffer.from(generated.rawData), fs.readFileSync(path.join(dir, 'request.der'))]) {
      // The independent standards oracle proves the unmodified request/signature.
      fs.writeFileSync(path.join(dir, 'request.pem'), csrPEM(der));
      openssl(['req', '-in', path.join(dir, 'request.pem'), '-verify', '-noout']);
      for (const newline of ['\n', '\r\n']) {
        const parsed = helper.convertCSRPEMToCSR(csrPEM(der, newline));
        assert.equal(await parsed.verify(crypto.webcrypto), true);
        assert.deepEqual(Buffer.from(parsed.rawData), der);
        for (const invalid of trailingCSRInputs(der)) {
          assert.throws(() => helper.convertCSRPEMToCSR(csrPEM(invalid.der, newline)), /Trailing CSR bytes/, invalid.name);
        }
      }
      // Truncation/invalid ASN.1 must still fail; no recovered or prefix-only CSR.
      for (const invalid of [der.subarray(0, -1), Buffer.from([0x30, 0x82, 0xff, 0xff])]) {
        assert.throws(() => helper.convertCSRPEMToCSR(csrPEM(invalid)), /Invalid CSR ASN\.1/);
      }
      assert.throws(() => helper.convertCSRPEMToCSR(csrPEM(der) + csrPEM(der)), /exactly one/);
    }
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});

test('certificate validation refuses wrong keys, expired/future validity, bad signature and missing usages', async () => {
  const req = createRequire(path.join(helperRoot, 'package.json')); req('reflect-metadata'); const x509 = req('@peculiar/x509');
  const pair = helper.generateKeyPair(); const other = helper.generateKeyPair();
  const keys = {
    privateKey: await crypto.webcrypto.subtle.importKey('pkcs8', pair.privateKey.export({ format: 'der', type: 'pkcs8' }), { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' }, false, ['sign']),
    publicKey: await crypto.webcrypto.subtle.importKey('spki', pair.publicKey.export({ format: 'der', type: 'spki' }), { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' }, true, ['verify']),
  };
  const cert = await helper.generateSelfSignedCodeSigningCertificate({ keyPair: pair, commonName: 'test', validityNotBefore: new Date(Date.now() - 60000), validityNotAfter: new Date(Date.now() + 86400000) });
  assert.throws(() => helper.validateSelfSignedCertificate(cert, other), /public key/);
  assert.throws(() => helper.validateSelfSignedCertificate(cert, { privateKey: other.privateKey, publicKey: pair.publicKey }), /mismatch/);
  assert.throws(() => helper.signBufferRSASHA256AndVerify(other.privateKey, cert, Buffer.from('x')), /not valid/);
  for (const delta of [-86400000, 86400000]) {
    const invalid = await helper.generateSelfSignedCodeSigningCertificate({ keyPair: pair, commonName: 'test', validityNotBefore: new Date(Date.now() + delta), validityNotAfter: new Date(Date.now() + delta + 10000) });
    assert.throws(() => helper.validateSelfSignedCertificate(invalid, pair), /validity/);
  }
  for (const extensions of [[], [new x509.KeyUsagesExtension(x509.KeyUsageFlags.digitalSignature, true)]]) {
    const missing = await x509.X509CertificateGenerator.createSelfSigned({ keys, name: 'CN=test', extensions }, crypto.webcrypto);
    assert.throws(() => helper.validateSelfSignedCertificate(helper.convertCertificatePEMToCertificate(missing.toString('pem')), pair), /Usage/);
  }
  const bytes = Buffer.from(cert.native.raw); bytes[bytes.length - 1] ^= 1;
  const tampered = `-----BEGIN CERTIFICATE-----\n${bytes.toString('base64')}\n-----END CERTIFICATE-----`;
  assert.throws(() => helper.validateSelfSignedCertificate(helper.convertCertificatePEMToCertificate(tampered), pair), /signature/);
  assert.throws(() => helper.convertCertificatePEMToCertificate(helper.convertCertificateToCertificatePEM(cert) + helper.convertCertificateToCertificatePEM(cert)), /exactly one/);
});

test('Expo Go online CSR flow, cache continuity and offline refusal use the adapted async API', async () => {
  const dir = temporary();
  const cliPath = path.join(cliRoot, 'build/src/utils/codesigning.js');
  const saved = new Map();
  const fake = (relative, exports) => {
    const file = path.join(cliRoot, 'build/src', relative + '.js');
    saved.set(file, require.cache[file]);
    require.cache[file] = { id: file, filename: file, loaded: true, exports };
  };
  saved.set(cliPath, require.cache[cliPath]);
  const env = { EXPO_OFFLINE: false };
  let requests = 0;
  try {
    openssl(['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', path.join(dir, 'ca-key'), '-out', path.join(dir, 'ca'), '-days', '60', '-subj', '/CN=Fixture Expo CA', '-addext', 'basicConstraints=critical,CA:TRUE']);
    const caPEM = fs.readFileSync(path.join(dir, 'ca'), 'utf8');
    const ca = helper.convertCertificatePEMToCertificate(caPEM);
    const key = helper.convertPrivateKeyPEMToPrivateKey(fs.readFileSync(path.join(dir, 'ca-key'), 'utf8'));
    fake('utils/env', { env });
    fake('api/user/UserSettings', { getExpoHomeDirectory: () => dir });
    fake('api/user/actions', { tryGetUserAsync: async () => ({ __typename: 'User', primaryAccount: { id: 'fixture-owner' }, accounts: [] }) });
    fake('api/graphql/queries/AppQuery', { AppQuery: { byIdAsync: async id => {
      assert.equal(id, 'fixture-project'); return { ownerAccount: { id: 'fixture-owner' }, scopeKey: '@fixture/project' };
    } } });
    fake('api/getExpoGoIntermediateCertificate', { getExpoGoIntermediateCertificateAsync: async () => caPEM });
    fake('api/getProjectDevelopmentCertificate', { getProjectDevelopmentCertificateAsync: async (id, csrPEM) => {
      requests++; assert.equal(id, 'fixture-project');
      const csr = helper.convertCSRPEMToCSR(csrPEM);
      assert.equal(await csr.verify(crypto.webcrypto), true); // fails if CLI forgot to await generateCSR
      return helper.convertCertificateToCertificatePEM(await helper.generateDevelopmentCertificateFromCSR(key, ca, csr, id, '@fixture/project'));
    } });
    delete require.cache[cliPath];
    const cli = require(cliPath);
    const exp = { extra: { eas: { projectId: 'fixture-project' } } };
    const result = await cli.getCodeSigningInfoAsync(exp, 'keyid="expo-root"');
    assert.equal(result.keyId, 'expo-go'); assert.equal(result.scopeKey, '@fixture/project');
    assert.equal(result.certificateChainForResponse.length, 2); assert.equal(requests, 1);
    const manifest = '{"id":"fixture","message":"東京"}';
    const signature = cli.signManifestString(manifest, result);
    assert(crypto.verify('sha256', Buffer.from(manifest), new crypto.X509Certificate(result.certificateForPrivateKey).publicKey, Buffer.from(signature, 'base64')));
    env.EXPO_OFFLINE = true;
    assert.deepEqual(await cli.getCodeSigningInfoAsync(exp, 'keyid="expo-root"'), result);
    assert.equal(await cli.getCodeSigningInfoAsync({ extra: { eas: { projectId: 'uncached' } } }, 'keyid="expo-root"'), null);
    assert.equal(requests, 1); // no hidden real or mocked provider call during offline recovery
    await assert.rejects(cli.getCodeSigningInfoAsync(exp, 'keyid="expo-go"'), /Invalid certificate requested/);
  } finally {
    for (const [file, prior] of saved) { if (prior) require.cache[file] = prior; else delete require.cache[file]; }
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('iOS identity consumer preserves CN/O/OU extraction without shell or keychain access', async () => {
  const spawnPath = createRequire(path.join(cliRoot, 'package.json')).resolve('@expo/spawn-async');
  const securityPath = path.join(cliRoot, 'build/src/run/ios/codeSigning/Security.js');
  const dir = temporary(); const prior = require.cache[spawnPath];
  try {
    openssl(['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', path.join(dir, 'key'), '-out', path.join(dir, 'cert'), '-days', '1', '-subj', '/CN=Apple Development: Fixture/O=Fixture Organization/OU=TEAM123']);
    require.cache[spawnPath] = { id: spawnPath, filename: spawnPath, loaded: true, exports: async (command, args) => {
      assert.equal(command, 'security'); assert.deepEqual(args, ['find-certificate', '-c', 'TEAM123', '-p']);
      return { stdout: fs.readFileSync(path.join(dir, 'cert'), 'utf8') };
    } };
    delete require.cache[securityPath]; const security = require(securityPath);
    assert.deepEqual(await security.resolveCertificateSigningInfoAsync('TEAM123'), { signingCertificateId: 'TEAM123', codeSigningInfo: 'Apple Development: Fixture', appleTeamName: 'Fixture Organization', appleTeamId: 'TEAM123' });
    assert.equal(security.extractSigningId('Apple Development: Fixture (TEAM123)'), 'TEAM123');
  } finally { if (prior) require.cache[spawnPath] = prior; else delete require.cache[spawnPath]; delete require.cache[securityPath]; fs.rmSync(dir, { recursive: true, force: true }); }
});
