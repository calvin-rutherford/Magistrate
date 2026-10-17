import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import * as intents from '../src/services/PendingIntentRouter';

// Exercise the real manager with injected native boundaries, never an Expo/APNs call.
function harness() {
  let owner: string | null = 'owner-a';
  let revision = 1;
  let granted = true;
  let lastResponse: any = null;
  let tokenListener: any;
  let responseListener: any;
  let resume: any;
  let permissionPrompts = 0;
  let permissionReads = 0;
  let tick: () => void = () => {};
  let permissionRead: () => Promise<any> = async () => ({ granted, canAskAgain: false });
  const registered: string[] = [];
  const expoOptions: any[] = [];
  const acknowledgements: string[][] = [];
  const client = {
    GatewayNetworkError: class extends Error {},
    getGatewaySessionRevision: () => revision,
    registerNativePushToken: async (token: string) => { registered.push(token); },
    revokeNativePushToken: async () => {},
    fetchNotificationEvents: async () => ({ events: [], unread: [] }),
    acknowledgeNotificationEvents: async (ids: string[]) => { acknowledgements.push(ids); },
  };
  const notifications = {
    DEFAULT_ACTION_IDENTIFIER: 'default', IosAuthorizationStatus: { PROVISIONAL: 3 },
    setNotificationHandler: () => {},
    getPermissionsAsync: () => { permissionReads++; return permissionRead(); },
    requestPermissionsAsync: async () => { permissionPrompts++; return { granted }; },
    getExpoPushTokenAsync: async (options: any) => { expoOptions.push(options); return { data: `ExpoPushToken[${options.devicePushToken?.data || 'first'}]` }; },
    addPushTokenListener: (callback: any) => { tokenListener = callback; return { remove() { tokenListener = null; } }; },
    addNotificationResponseReceivedListener: (callback: any) => { responseListener = callback; return { remove() {} }; },
    getLastNotificationResponse: () => lastResponse,
    clearLastNotificationResponse: () => { lastResponse = null; },
  };
  const modules: Record<string, any> = {
    'expo-notifications': notifications,
    'expo-device': { isDevice: true },
    'expo-constants': { default: { easConfig: { projectId: 'project-id' } }, __esModule: true },
    'react-native': { Platform: { OS: 'ios' }, AppState: { currentState: 'active', addEventListener: (_: any, callback: any) => { resume = callback; return { remove() {} }; } } },
    './PendingIntentRouter': intents,
    './MagiConversationSession': { getMagiConversationPrincipal: () => owner },
    '../api/client': client,
  };
  const output = ts.transpileModule(readFileSync(new URL('../src/services/NotificationManager.ts', import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  const exports: any = {};
  vm.runInNewContext(output, { exports, require: (name: string) => { assert.ok(modules[name], name); return modules[name]; }, console: { error() {} }, process, setInterval: (callback: () => void) => { tick = callback; return 1; }, clearInterval() {}, Date, Set });
  return { manager: exports.notificationManager, registered, acknowledgements, expoOptions, client,
    get permissionPrompts() { return permissionPrompts; },
    get permissionReads() { return permissionReads; },
    tick: () => tick(),
    rejectCleanup: () => { notifications.clearLastNotificationResponse = () => Promise.reject(new Error('native cleanup rejected')); },
    setDenied: () => { granted = false; },
    deferPermission: (promise: Promise<any>) => { permissionRead = () => promise; },
    changeOwner: () => { owner = 'owner-b'; revision++; },
    rotateToken: () => tokenListener?.({ type: 'ios', data: 'rotated-apns-token' }),
    resume: () => { resume('background'); resume('active'); },
    tap: (id: string, action = 'default') => {
      lastResponse = { actionIdentifier: action, notification: { request: { identifier: id, content: { data: { intent_version: 1, target_type: 'attention', target_id: id, item_id: id, route: `/attention?item=${id}` } } } } };
      responseListener?.(lastResponse);
    },
    get lastResponse() { return lastResponse; },
  };
}
const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };

test('cold tap waits through session restoration, consumes once, and clears OS replay only after routing', () => {
  const h = harness();
  h.tap('cold-decision');
  h.manager.installNotificationRouting();
  assert.equal(intents.getPendingIntent()?.targetType, 'attention');
  assert.equal(h.acknowledgements.length, 0);
  assert.ok(h.lastResponse);
  const intent = intents.consumePendingIntent();
  assert.equal(intent?.deliveryId, 'cold-decision');
  assert.equal(h.lastResponse, null);
  h.tap('cold-decision');
  assert.equal(intents.consumePendingIntent(), null);
  h.tap('warm-decision');
  assert.equal(intents.consumePendingIntent()?.deliveryId, 'warm-decision');
  h.tap('not-an-approval', 'approve');
  assert.equal(intents.consumePendingIntent(), null);
  assert.equal(h.acknowledgements.length, 0);
});

test('rejected OS cleanup cannot lose an authenticated route or leak an unhandled rejection', async () => {
  const h = harness();
  h.manager.installNotificationRouting();
  h.rejectCleanup();
  h.tap('cleanup-rejected');
  assert.equal(intents.consumePendingIntent()?.deliveryId, 'cleanup-rejected');
  await flush();
  assert.ok(h.lastResponse); // No claim that the OS cleared it.
  h.tap('cleanup-rejected');
  assert.equal(intents.consumePendingIntent(), null);
});

test('a superseded registration waits for the new lifecycle poll instead of recursively registering', async () => {
  const h = harness();
  let resolve!: (permission: any) => void;
  h.deferPermission(new Promise(yes => { resolve = yes; }));
  h.manager.startMonitoring();
  h.manager.stopMonitoring();
  h.changeOwner();
  h.manager.startMonitoring();
  resolve({ granted: true });
  await flush();
  assert.equal(h.permissionReads, 1);
  assert.equal(h.registered.length, 0);
  assert.equal(h.manager.getPushStatus(), 'not-started');
  h.tick(); await flush();
  assert.equal(h.permissionReads, 2);
  assert.deepEqual(h.registered, ['ExpoPushToken[first]']);
  h.manager.stopMonitoring();
});

test('native token rotation registers an Expo token, not APNs, and rechecks permission on resume', async () => {
  const h = harness();
  h.manager.startMonitoring();
  await flush();
  assert.deepEqual(h.registered, ['ExpoPushToken[first]']);
  h.rotateToken(); await flush();
  assert.deepEqual(h.registered, ['ExpoPushToken[first]', 'ExpoPushToken[rotated-apns-token]']);
  assert.equal(h.expoOptions[1].devicePushToken.data, 'rotated-apns-token');
  h.setDenied(); h.resume(); await flush();
  assert.equal(h.manager.getPushStatus(), 'permission-denied');
  assert.equal(h.permissionPrompts, 0);
  h.manager.stopMonitoring();
});

test('permission denial and an owner change during permission restoration never register', async () => {
  const denied = harness(); denied.setDenied();
  assert.equal(await denied.manager.registerNativePushToken(), 'permission-denied');
  assert.equal(denied.registered.length, 0);
  const h = harness();
  let resolve!: (permission: any) => void;
  h.deferPermission(new Promise(yes => { resolve = yes; }));
  const registration = h.manager.registerNativePushToken();
  h.changeOwner(); resolve({ granted: true });
  await registration;
  assert.equal(h.registered.length, 0);
});

test('offline registration retries are throttled; stopping monitoring clears prior-principal state', async () => {
  const h = harness();
  let calls = 0;
  h.client.registerNativePushToken = async () => { calls++; throw new h.client.GatewayNetworkError(); };
  await h.manager.registerNativePushToken();
  assert.equal(h.manager.getPushStatus(), 'offline');
  await h.manager.registerNativePushToken();
  assert.equal(calls, 1);
  h.manager.stopMonitoring();
  assert.equal(h.manager.getPushStatus(), 'not-started');
  assert.equal(h.manager.getUnreadEvents().length, 0);
});
