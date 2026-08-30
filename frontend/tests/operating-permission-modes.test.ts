import assert from 'node:assert/strict';
import test from 'node:test';
import AsyncStorage from '@react-native-async-storage/async-storage';

const storage = new Map<string, string>();
(globalThis as any).window = { localStorage: {
  getItem: (key: string) => storage.get(key) || null,
  setItem: (key: string, value: string) => { storage.set(key, value); },
  removeItem: (key: string) => { storage.delete(key); },
} };

import {
  OPERATING_PERMISSION_MODE_KEY,
  loadOperatingPermissionMode,
  saveOperatingPermissionMode,
} from '../src/services/OperatingPermissionModes';

test('operating permission modes persist durably and reject unknown values', async () => {
  await AsyncStorage.removeItem(OPERATING_PERMISSION_MODE_KEY);
  assert.equal(await loadOperatingPermissionMode(), 'moderate');
  await saveOperatingPermissionMode('restricted');
  assert.equal(await AsyncStorage.getItem(OPERATING_PERMISSION_MODE_KEY), 'restricted');
  assert.equal(await loadOperatingPermissionMode(), 'restricted');
  await assert.rejects(() => saveOperatingPermissionMode('merge' as never), /Unsupported/);
});
