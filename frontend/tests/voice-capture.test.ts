import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

function captureHarness(permission: Promise<{ granted: boolean }>) {
  let records = 0;
  let stops = 0;
  let recording = false;
  const modes: any[] = [];
  const recorder = {
    prepareToRecordAsync: async () => {},
    getCurrentInput: async () => ({ uid: 'headset', name: 'Headset', type: 'Bluetooth' }),
    record: () => { records++; recording = true; },
    stop: async () => { stops++; recording = false; },
    getStatus: () => ({ isRecording: recording }),
  };
  const modules: Record<string, any> = {
    react: { useRef: (value: any) => ({ current: value }), useState: (value: any) => [value, () => {}], useCallback: (callback: any) => callback, useEffect: () => {} },
    'expo-audio': { AudioModule: { getRecordingPermissionsAsync: () => permission, requestRecordingPermissionsAsync: () => permission }, RecordingPresets: { HIGH_QUALITY: {} }, setAudioModeAsync: async (mode: any) => { modes.push(mode); }, useAudioRecorder: () => recorder, useAudioRecorderState: () => ({ isRecording: false }) },
    'react-native': { Platform: { OS: 'ios' } },
    '../services/VoiceInputModes': { DEFAULT_VOICE_INPUT_MODE: 'automatic' },
  };
  const output = ts.transpileModule(readFileSync(new URL('../src/input/VoiceInputAdapter.ts', import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  const exports: any = {};
  vm.runInNewContext(output, { exports, require: (name: string) => modules[name] });
  return { capture: exports.useVoiceInputAdapter(), modes, get records() { return records; }, get stops() { return stops; } };
}

test('cancelling while microphone consent is pending cannot start capture after consent resolves', async () => {
  let consent!: (result: { granted: boolean }) => void;
  const h = captureHarness(new Promise(resolve => { consent = resolve; }));
  const start = h.capture.start();
  await h.capture.cancel();
  consent({ granted: true });
  await start;
  assert.equal(h.records, 0);
  assert.equal(h.modes.at(-1).allowsRecording, false);
});

test('capture cancellation reads the native recorder, not the previous React recording snapshot', async () => {
  const h = captureHarness(Promise.resolve({ granted: true }));
  await h.capture.start();
  assert.equal(h.records, 1);
  await h.capture.cancel();
  assert.equal(h.stops, 1);
  assert.equal(h.modes.at(-1).allowsRecording, false);
});

test('microphone denial is recoverable and never starts recording', async () => {
  const h = captureHarness(Promise.resolve({ granted: false }));
  await assert.rejects(h.capture.start(), /Microphone permission was denied/);
  assert.equal(h.records, 0);
});
