const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const read = relative => fs.readFileSync(path.join(__dirname, '..', relative), 'utf8');

test('custom theme uses persisted preferences and has safe upload controls', () => {
  const preferences = read('src/services/ChatPreferences.ts');
  const chat = read('app/(tabs)/chat.tsx');
  assert.match(preferences, /CHAT_CUSTOM_BACKGROUND_KEY/);
  assert.match(preferences, /saveCustomBackground/);
  assert.match(preferences, /removeCustomBackground/);
  assert.match(chat, /custom-background-upload/);
  assert.match(chat, /custom-background-preview/);
  assert.match(chat, /custom-background-remove/);
  assert.match(chat, /10 \* 1024 \* 1024/);
  assert.match(chat, /result\.canceled/);
});

test('custom background rendering is isolated from built-in weather scenes', () => {
  const theme = read('src/services/environmentTheme.ts');
  assert.match(theme, /sceneKey === 'custom'/);
  assert.match(theme, /sceneImage: isCustom \? \{ uri: customImageUri \}/);
  assert.match(theme, /sceneKey !== 'custom'/);
});
