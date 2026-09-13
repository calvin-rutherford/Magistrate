import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import { evaluateFriendBetaRelease } from '../scripts/friend-beta-release-preflight.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = name => JSON.parse(fs.readFileSync(path.join(ROOT, name), 'utf8'));
const clone = value => JSON.parse(JSON.stringify(value));
const base = () => ({
  profile: 'preview',
  env: {
    EXPO_PUBLIC_GATEWAY_URL: 'https://beta.example.test/api/v1',
    EXPO_PUBLIC_MAGI_NATIVE_CHAT_ENABLED: 'true',
    EXPO_PUBLIC_MAGI_LEGACY_CHAT_ENABLED: 'false',
  },
  app: read('app.json'),
  eas: read('eas.json'),
  packageJson: read('package.json'),
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
  const ready = evaluateFriendBetaRelease(fixture);
  assert.equal(ready.result, 'PASS', ready.failures.join('\n'));
  assert.ok(ready.checks.includes('testflight-production-profile'));
});

test('preview requires an explicit native-only chat transport selection', () => {
  const fixture = base();
  fixture.eas = clone(fixture.eas);
  fixture.eas.build.preview.env.EXPO_PUBLIC_MAGI_NATIVE_CHAT_ENABLED = 'false';
  delete fixture.env.EXPO_PUBLIC_MAGI_NATIVE_CHAT_ENABLED;
  fixture.env.EXPO_PUBLIC_MAGI_LEGACY_CHAT_ENABLED = 'true';
  const result = evaluateFriendBetaRelease(fixture);
  assert.equal(result.result, 'FAIL');
  assert.ok(result.failures.some(item => item.startsWith('eas-native-only-env:')));
  assert.ok(result.failures.some(item => item.startsWith('native-chat-selected:')));
  assert.ok(result.failures.some(item => item.startsWith('legacy-chat-disabled:')));
});
