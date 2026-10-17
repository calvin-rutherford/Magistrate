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
  assert.match(chatSource(), /Accent color appears only when work is active or needs attention/);
  assert.doesNotMatch([chatSource(), read('app/(tabs)/account.tsx'), read('app/(tabs)/attention.tsx')].join('\n'), /fontFamily:/);
});

test('Account exposes connected profile, plan, preferences, lifecycle and support surfaces', () => {
  const account = read('app/(tabs)/account.tsx');
  for (const contract of [
    'account-profile-name-input', 'PLAN, CREDITS & BILLING', 'CONNECTED OAUTH PROVIDERS',
    'ATTENTION NOTIFICATIONS', 'VOICE & SPEECH SYNTHESIS', 'APPEARANCE',
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

test('normal conversation, drawer and settings copy is quiet and unbranded', () => {
  const chat = chatSource();
  const account = read('app/(tabs)/account.tsx');
  const legacyDrawer = read('src/components/GlassDrawer.tsx');
  for (const source of [chat, account, legacyDrawer]) {
    assert.doesNotMatch(source, />\s*(?:Magi|Magistrate)\s*</i);
  }
  assert.match(chat, /placeholder="Message"/);
  assert.match(chat, /accessibilityLabel="Assistant conversation"/);
  assert.match(chat, /executionProfiles\.some\(profile => profile\.available\)/,
    'execution controls stay hidden without observed capabilities');
});

test('conversation UI keeps delivery, response, and job state distinct', () => {
  const chat = chatSource();
  assert.match(chat, /const saved = Boolean\(message\.serverId\)/);
  assert.match(chat, /Saved · response failed/);
  assert.match(chat, />Not sent</);
  assert.match(chat, /blocked: 'Job blocked'/);
  assert.match(chat, /failed: 'Job failed'/);
});

test('notification decisions are revision-pinned and test evidence never claims phone delivery', () => {
  const attention = read('app/(tabs)/attention.tsx');
  const account = read('app/(tabs)/account.tsx');
  const client = read('src/api/client.ts');
  assert.match(attention, /compareDecisionDestination\(decisionParam, revisionParam/);
  assert.match(attention, /action\?\.status === 'available' && !navigationBlocked/);
  assert.match(attention, /confirmation\.source_revision !== item\.action\.source_revision/);
  assert.match(account, /Phone delivery: Unknown · Seen on phone: Unknown/);
  assert.match(account, /same test identity will be reused/i);
  assert.match(client, /observed_phone_delivery !== 'unknown'/);
  assert.match(client, /owner_saw_notification !== 'unknown'/);
});

function chatSource() { return read('app/(tabs)/chat.tsx'); }
