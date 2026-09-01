const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const read = relative => fs.readFileSync(path.join(__dirname, '..', relative), 'utf8');
test('backgrounds are bundled and weather is explicitly configured', () => {
  for (const period of ['dawn', 'day', 'dusk', 'night']) assert.ok(fs.existsSync(path.join(__dirname, '..', `assets/images/environment/${period}.png`)));
  const weather = read('src/services/weather.ts');
  assert.match(weather, /EXPO_PUBLIC_WEATHER_LATITUDE/); assert.match(weather, /EXPO_PUBLIC_WEATHER_LONGITUDE/); assert.match(weather, /EXPO_PUBLIC_AMBIENT_DATA_SAVER/);
  assert.doesNotMatch(read('src/services/environmentTheme.ts'), /images\.unsplash\.com/);
});
test('voice metering uses recorder data, never randomized amplitude', () => {
  const adapter = read('src/input/VoiceInputAdapter.ts'); assert.match(adapter, /(status|recorderState)\.metering/); assert.doesNotMatch(adapter, /Math\.random/);
  assert.match(read('src/services/SpeechActivityAdapter.ts'), /gate/);
});
