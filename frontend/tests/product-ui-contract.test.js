const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const read = relative => fs.readFileSync(path.join(__dirname, '..', relative), 'utf8');

test('customer navigation is canonical and excludes legacy developer destinations', () => {
  const chat = read('app/(tabs)/chat.tsx');
  for (const label of ['Chat', 'Projects', 'Fleet', 'Activity', 'Attention', 'Account & Settings']) {
    assert.match(chat, new RegExp(label.replace(/[&]/g, '\\&')));
  }
  const drawer = chat.slice(chat.indexOf('function DrawerPanel'), chat.indexOf('function fleetTimestamp'));
  assert.doesNotMatch(drawer, /Diagnostics|Terminal|Agents|Situation Room|Pull Requests/);
  assert.doesNotMatch(drawer, /key: 'connections'/);
});

test('resting customer surfaces are flat and reserve spectral color for state', () => {
  const background = read('src/components/EnvironmentBackground.tsx');
  const surface = read('src/components/GlassSurface.tsx');
  assert.doesNotMatch(background, /ImageBackground|WeatherOverlay|LinearGradient|BlurView/);
  assert.doesNotMatch(surface, /LinearGradient|backdropFilter|BlurView/);
  assert.match(background, /monochrome at rest/);
  assert.match(chatSource(), /Spectral color appears only when work is active or needs attention/);
  assert.doesNotMatch([chatSource(), read('app/(tabs)/account.tsx'), read('app/(tabs)/attention.tsx')].join('\n'), /fontFamily:/);
});

test('Account exposes connected profile, plan, preferences, lifecycle and support surfaces', () => {
  const account = read('app/(tabs)/account.tsx');
  for (const contract of [
    'account-profile-name-input', 'PLAN, CREDITS & BILLING', 'CONNECTED OAUTH PROVIDERS',
    'CAPTAIN ATTENTION NOTIFICATIONS', 'VOICE & SPEECH SYNTHESIS', 'APPEARANCE',
    'Privacy & security', 'Legal & license', 'Support', 'account-version',
    'account-logout', 'account-delete-confirmation', 'account-delete',
  ]) assert.match(account, new RegExp(contract.replace(/[&]/g, '\\&')));
  assert.match(account, /Permanently erases account data/);
  assert.match(account, /deleteGatewayAccount/);
  assert.doesNotMatch(account, /account-custom-background-upload|Dusk Mountain|Rain Storm/);
});

test('project creation and destructive controls have accessible, product-safe copy', () => {
  const chat = chatSource();
  assert.match(chat, /accessibilityLabel="Create standalone project"/);
  assert.match(chat, /Request cancellation of this objective\?/);
  assert.match(chat, /does not signal a process or claim success early/);
  assert.doesNotMatch(chat, /OPEN LEGACY AGENT CHAT|TAP SPHERE|COMMAND NAVIGATION/);
});

function chatSource() { return read('app/(tabs)/chat.tsx'); }
