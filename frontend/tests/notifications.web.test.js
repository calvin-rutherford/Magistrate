const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const path = require('node:path');
const test = require('node:test');
const puppeteer = require('puppeteer-core');

const PORT = Number(process.env.MAGISTRATE_WEB_TEST_PORT) || 8096;
const URL = `http://127.0.0.1:${PORT}/attention`;
let server;
let browser;

const events = [
  { id: 'question-1', provider: 'firstmate', title: 'Deployment choice', subtitle: 'Choose the rollout window.', priority: 'HIGH', status: 'needs-decision', url: '/attention?item=question-1', requires_action: true, notification_kind: 'captain_question' },
  { id: 'pr-2', provider: 'github', title: 'Review ready', subtitle: 'A pull request needs your review.', priority: 'MEDIUM', status: 'ready', url: '/attention?item=pr-2', requires_action: true, notification_kind: 'pr_ready' }
];

async function waitForServer() {
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    try { if ((await fetch(URL)).ok) return; } catch {}
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  throw new Error('Expo web server did not become ready');
}

test.before(async () => {
  server = spawn(path.join(process.cwd(), 'node_modules', '.bin', 'expo'), ['start', '--web', '--port', String(PORT)], {
    cwd: process.cwd(), env: { ...process.env, CI: '1' }, stdio: 'ignore'
  });
  await waitForServer();
  browser = await puppeteer.launch({ executablePath: '/usr/bin/google-chrome', headless: true, args: ['--no-sandbox'] });
});

test.after(async () => {
  await browser?.close();
  server?.kill('SIGTERM');
});

async function openAttention({ notificationMode, eventBatch = events } = {}) {
  const page = await browser.newPage();
  await page.evaluateOnNewDocument(({ mode, batch }) => {
    window.__notificationEvents = [...batch];
    window.__notificationCalls = [];
    window.__browserNotifications = [];
    const nativeFetch = window.fetch.bind(window);
    window.fetch = (resource, options) => {
      const url = typeof resource === 'string' ? resource : resource.url;
      if (url.includes('/api/v1/auth/session')) {
        const payload = options?.method === 'POST' ? { session_token: 'browser-test-session', token_type: 'Bearer', expires_at: 4102444800, scopes: ['read', 'account', 'providers', 'notifications', 'voice', 'command'], user_id: 'default_user' } : { authenticated: true, expires_at: 4102444800, scopes: ['read', 'account', 'providers', 'notifications', 'voice', 'command'], user_id: 'default_user' };
        return Promise.resolve(new Response(JSON.stringify(payload), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      if (url.includes('/api/v1/notifications/events/ack')) {
        window.__notificationCalls.push({ url, method: options?.method, body: options?.body });
        return Promise.resolve(new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      if (url.includes('/api/v1/notifications/events')) {
        window.__notificationCalls.push({ url, method: options?.method });
        const next = window.__notificationEvents.shift() || [];
        return Promise.resolve(new Response(JSON.stringify({ events: next }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      if (url.includes('/api/v1/attention/unified')) {
        return Promise.resolve(new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      return nativeFetch(resource, options);
    };

    if (mode === 'granted' || mode === 'denied') {
      class FakeNotification {
        static permission = mode;
        static requestPermission = async () => mode;
        constructor(title, options) {
          window.__browserNotifications.push({ title, body: options.body });
        }
        close() {}
      }
      Object.defineProperty(window, 'Notification', { configurable: true, value: FakeNotification });
    } else {
      Object.defineProperty(window, 'Notification', { configurable: true, value: undefined });
    }
  }, { mode: notificationMode, batch: [eventBatch] });
  await page.goto(URL, { waitUntil: 'networkidle0' });
  return page;
}

test('new attention transition delivers one real browser notification and acknowledges it', async () => {
  const page = await openAttention({ notificationMode: 'granted', eventBatch: [events[0]] });
  await page.waitForFunction(() => window.__browserNotifications.length === 1);
  assert.equal(await page.evaluate(() => window.__browserNotifications[0].title), 'Your answer is needed');
  assert.equal(await page.$('[data-testid="notification-dismiss-question-1"]'), null);
  await page.waitForFunction(() => window.__notificationCalls.some(call => call.url.includes('/events/ack')));
  await page.close();
});

test('denied or unsupported browser notifications fall back to a dismissible stack', async () => {
  for (const notificationMode of ['denied', 'unsupported']) {
    const page = await openAttention({ notificationMode, eventBatch: events });
    await page.waitForSelector('[data-testid="notification-dismiss-question-1"]');
    await page.waitForSelector('[data-testid="notification-dismiss-pr-2"]');
    assert.equal(await page.evaluate(() => window.__browserNotifications.length), 0);
    await page.click('[data-testid="notification-dismiss-question-1"]');
    await page.waitForFunction(() => !document.querySelector('[data-testid="notification-dismiss-question-1"]'));
    assert.ok(await page.$('[data-testid="notification-dismiss-pr-2"]'));
    await page.close();
  }
});
