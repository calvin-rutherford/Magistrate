import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

import { evaluateFriendBetaRelease } from '../scripts/friend-beta-release-preflight.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = name => JSON.parse(fs.readFileSync(path.join(ROOT, name), 'utf8'));
const clone = value => JSON.parse(JSON.stringify(value));
const base = () => ({
  profile: 'preview',
  env: {
    EXPO_PUBLIC_GATEWAY_URL: 'https://beta.example.test/api/v1',
  },
  app: read('app.json'),
  eas: read('eas.json'),
  packageJson: read('package.json'),
});

test('the patched Xcode UUID dependency preserves its CommonJS v4-only interface', () => {
  const require = createRequire(import.meta.url);
  const xcode = require('xcode');
  const project = xcode.project('not-read.pbxproj');
  project.hash = { project: { objects: {} } };
  const ids = new Set(Array.from({ length: 100 }, () => project.generateUuid()));
  assert.equal(ids.size, 100);
  assert.ok([...ids].every(id => /^[A-F0-9]{24}$/.test(id)));
  const xcodeRequire = createRequire(require.resolve('xcode'));
  assert.equal(xcodeRequire('uuid/package.json').version, '11.1.1');
});

test('the committed preview configuration passes repository-side Friend Beta preflight', () => {
  const result = evaluateFriendBetaRelease(base());
  assert.equal(result.result, 'PASS', result.failures.join('\n'));
  assert.ok(result.checks.includes('physical-preview-profile'));
  assert.ok(result.checks.includes('ios-background-modes'));
  assert.ok(result.checks.includes('eas-pre-install-gate'));
  assert.deepEqual(result.failures, []);
});

test('preflight rejects unsafe device endpoints and secret-shaped public variables', () => {
  const fixture = base();
  fixture.env.EXPO_PUBLIC_GATEWAY_URL = 'http://localhost:8000/api/v1?token=nope';
  fixture.env.EXPO_PUBLIC_PROVIDER_API_KEY = 'must-not-be-public';
  const result = evaluateFriendBetaRelease(fixture);
  assert.equal(result.result, 'FAIL');
  assert.ok(result.failures.some(item => item.startsWith('gateway-https-public:')));
  assert.ok(result.failures.some(item => item.startsWith('gateway-url-public-config:')));
  assert.ok(result.failures.some(item => item.startsWith('no-public-secret-names:')));
  assert.ok(!JSON.stringify(result).includes('must-not-be-public'));
});

test('preflight rejects duplicate or unsupported iOS background declarations', () => {
  const fixture = base();
  fixture.app = clone(fixture.app);
  fixture.app.expo.ios.infoPlist.UIBackgroundModes = ['remote-notification', 'audio'];
  const result = evaluateFriendBetaRelease(fixture);
  assert.equal(result.result, 'FAIL');
  assert.ok(result.failures.some(item => item.startsWith('ios-background-modes:')));
});

test('production remains fail-closed until a real App Store Connect record is linked', () => {
  const fixture = base();
  fixture.profile = 'production';
  const blocked = evaluateFriendBetaRelease(fixture);
  assert.equal(blocked.result, 'FAIL');
  assert.ok(blocked.failures.some(item => item.startsWith('app-store-connect-link:')));

  fixture.eas = clone(fixture.eas);
  fixture.eas.submit.production.ios = { ascAppId: '1234567890' };
  fixture.env = {
    EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID: 'repository-test.apps.googleusercontent.com',
    EXPO_PUBLIC_GOOGLE_IOS_REVERSED_CLIENT_ID: 'com.googleusercontent.apps.repository-test',
    EXPO_PUBLIC_GATEWAY_URL: 'https://api.magistrate.com/api/v1',
    APPLE_TEAM_ID: 'ABCD123456',
    EXPO_PUBLIC_PRIVACY_URL: 'https://magistrate.com/privacy',
    EXPO_PUBLIC_SUPPORT_URL: 'https://magistrate.com/support',
  };
  const ready = evaluateFriendBetaRelease(fixture);
  assert.equal(ready.result, 'PASS', ready.failures.join('\n'));
  assert.ok(ready.checks.includes('testflight-production-profile'));
  fixture.env.EXPO_PUBLIC_PRIVACY_URL = 'https://127.0.0.1/private?secret=no';
  const unsafe = evaluateFriendBetaRelease(fixture);
  assert.equal(unsafe.result, 'FAIL');
  assert.ok(unsafe.failures.some(item => item.startsWith('privacy-url:')));
  assert.ok(!JSON.stringify(unsafe).includes('secret=no'));
});
