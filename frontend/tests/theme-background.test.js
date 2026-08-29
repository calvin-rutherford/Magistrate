const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const read = relative => fs.readFileSync(path.join(__dirname, '..', relative), 'utf8');

test('custom theme uses persisted preferences and has safe upload controls', () => {
  const preferences = read('src/services/ChatPreferences.ts');
  const account = read('app/(tabs)/account.tsx');
  assert.match(preferences, /CHAT_CUSTOM_BACKGROUND_KEY/);
  assert.match(preferences, /saveCustomBackground/);
  assert.match(preferences, /removeCustomBackground/);
  assert.match(account, /account-custom-background-upload/);
  assert.match(account, /customBackgroundPreview/);
  assert.match(account, /account-custom-background-remove/);
  assert.match(account, /10 \* 1024 \* 1024/);
  assert.match(account, /pickerResult\.canceled/);
});

test('the MVP appearance defaults and persistence contract are explicit', () => {
  const preferences = read('src/services/ChatPreferences.ts');
  assert.match(preferences, /themeMode: 'system'/);
  assert.match(preferences, /background: 'auto'/);
  assert.match(preferences, /storedBackground === 'custom'/);
  assert.match(preferences, /removeItem\(CHAT_CUSTOM_BACKGROUND_KEY\)/);
  const settings = read('app/(tabs)/chat.tsx');
  assert.match(settings, /settings-usage-section/);
  assert.match(settings, /Authenticated quota data only/);
  assert.doesNotMatch(settings, /key: 'usage'/);
  assert.match(settings, /settings-execution-section/);
  assert.match(settings, /settings-theme-options/);
});

test('custom background rendering is isolated from built-in weather scenes', () => {
  const theme = read('src/services/environmentTheme.ts');
  assert.match(theme, /sceneKey === 'custom'/);
  assert.match(theme, /sceneImage: isCustom \? \{ uri: customImageUri \}/);
  assert.match(theme, /activeSceneKey !== 'custom'/);
});
