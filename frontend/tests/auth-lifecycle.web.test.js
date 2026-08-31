const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const path = require('node:path');
const test = require('node:test');
const puppeteer = require('puppeteer-core');

const PORT = Number(process.env.MAGISTRATE_WEB_TEST_PORT) || 8197;
const URL = `http://127.0.0.1:${PORT}/chat`;
let server;
let browser;

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
  browser = await puppeteer.launch({
    executablePath: process.env.PUPPETEER_EXECUTABLE_PATH || '/usr/bin/google-chrome',
    headless: true,
    args: ['--no-sandbox']
  });
});

test.after(async () => {
  await browser?.close();
  server?.kill('SIGTERM');
});

async function open(mode = 'normal', preserveStorage = false) {
  const page = await browser.newPage();
  await page.evaluateOnNewDocument(({ mode, preserveStorage }) => {
    if (!preserveStorage && !sessionStorage.getItem('__auth_test_initialized')) {
      localStorage.clear();
      sessionStorage.clear();
      sessionStorage.setItem('__auth_test_initialized', '1');
    }
    const nativeFetch = window.fetch.bind(window);
    const state = { mode, valid: false, calls: [], authCalls: [] };
    const expiresAt = mode === 'expiry' ? Math.floor(Date.now() / 1000) + 20 : 4102444800;
    let validationFailures = mode === 'validation-failure' ? 1 : 0;
    window.__authLifecycle = state;
    const json = (payload, status = 200) => Promise.resolve(new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } }));
    window.fetch = (resource, options = {}) => {
      const url = typeof resource === 'string' ? resource : resource.url;
      const method = options.method || 'GET';
      if (url.includes('/api/v1/auth/session')) {
        state.authCalls.push({ method, body: options.body || null });
        if (method === 'POST') {
          let body = {};
          try { body = JSON.parse(options.body || '{}'); } catch {}
          if (body.bootstrap_secret !== 'valid-bootstrap') return json({ detail: 'Invalid session bootstrap credential' }, 401);
          state.valid = true;
          return json({ session_token: 'browser-test-session', token_type: 'Bearer', expires_at: expiresAt, scopes: ['read', 'account', 'providers', 'notifications', 'voice', 'command'], user_id: 'default_user' });
        }
        const authorization = options.headers?.Authorization || options.headers?.get?.('Authorization');
        if (validationFailures > 0) {
          validationFailures -= 1;
          return json({ detail: 'Transient session validation failure' }, 503);
        }
        if (authorization === 'Bearer browser-test-session') {
          state.valid = true;
          return json({ authenticated: true, expires_at: expiresAt, scopes: ['read', 'account', 'providers', 'notifications', 'voice', 'command'], user_id: 'default_user' });
        }
        return json({ detail: 'Invalid or expired session' }, 401);
      }
      if (url.includes('/api/v1/')) {
        const authorization = options.headers?.Authorization || options.headers?.get?.('Authorization');
        state.calls.push({ url, method, authorization: authorization || null });
        if (!state.valid || authorization !== 'Bearer browser-test-session') return json({ detail: 'Authentication required' }, 401);
        if (mode === 'active-401' && url.includes('/captain/prompt')) return json({ detail: 'Invalid or expired session' }, 401);
        if (mode === 'scope-403' && url.includes('/captain/prompt')) return json({ detail: 'Missing required scope: command' }, 403);
        if (url.includes('/captain/prompt')) return json({ status: 'submitted', target: 'captain', response: 'Authenticated reply from Firstmate.' });
        if (url.includes('/execution/capabilities')) return json({ harnesses: [], profiles: [], source: 'test', configured: false });
        if (url.includes('/execution/settings')) return json({ profile_id: null, switching_behavior: 'migrate', unavailable_behavior: 'error', migration_supported: false, credentials: [] });
        if (url.includes('/notifications/events')) return json({ events: [] });
        if (url.includes('/recent-activity')) return json({ items: [], sources: { firstmate: 'available', github: 'available' } });
        if (url.includes('/auth/providers')) return json([]);
        if (url.includes('/agents/') && url.includes('/history')) return json({ target: 'captain', messages: [] });
        if (url.endsWith('/agents')) return json([]);
        if (url.includes('/health')) return json({ status: 'healthy', service: 'gateway', herdr_socket_connected: true });
        if (url.includes('/attention')) return json([]);
        if (url.includes('/usage')) return json({ source: 'quota-axi', providers: [] });
        return json({ status: 'ok' });
      }
      return nativeFetch(resource, options);
    };
  }, { mode, preserveStorage });
  await page.goto(URL, { waitUntil: 'networkidle0' });
  await page.evaluate(() => { const toast = document.getElementById('error-toast'); if (toast) toast.style.pointerEvents = 'none'; });
  return page;
}

async function connect(page) {
  await page.waitForSelector('[data-testid="bootstrap-secret"]');
  await page.type('[data-testid="bootstrap-secret"]', 'valid-bootstrap');
  await page.click('[data-testid="connect-session"]');
  await page.waitForSelector('[data-testid="branded-chat-shell"]');
  await page.waitForSelector('[data-testid="captain-prompt"]');
  await page.waitForSelector('[data-testid="model-menu-button"]');
  await page.waitForFunction(() => window.__authLifecycle.calls.some(call => call.url.includes('/execution/settings')));
}

test('fresh browser gates protected routes, rejects invalid bootstrap, then reaches usable Chat after validation', async () => {
  const page = await open();
  await page.waitForSelector('[data-testid="session-status"]');
  assert.match(await page.$eval('[data-testid="session-status"]', node => node.textContent), /SESSION REQUIRED/);
  assert.equal(await page.$('[data-testid="branded-chat-shell"]'), null);
  assert.equal(await page.evaluate(() => window.__authLifecycle.calls.length), 0);

  await page.type('[data-testid="bootstrap-secret"]', 'wrong');
  await page.click('[data-testid="connect-session"]');
  await page.waitForSelector('[data-testid="session-error"]');
  assert.equal(await page.$('[data-testid="branded-chat-shell"]'), null);
  assert.equal(await page.evaluate(() => window.__authLifecycle.calls.length), 0);

  await page.click('[data-testid="bootstrap-secret"]');
  await page.keyboard.down('Control');
  await page.keyboard.press('A');
  await page.keyboard.up('Control');
  await page.type('[data-testid="bootstrap-secret"]', 'valid-bootstrap');
  await page.click('[data-testid="connect-session"]');
  await page.waitForSelector('[data-testid="branded-chat-shell"]');
  await page.type('[data-testid="captain-prompt"]', 'status please');
  await page.click('[data-testid="send-captain-prompt"]');
  await page.waitForFunction(() => window.__authLifecycle.calls.some(call => call.url.includes('/captain/prompt')));
  await page.waitForFunction(() => document.body.innerText.includes('Authenticated reply from Firstmate.'));
  assert.ok(await page.evaluate(() => window.__authLifecycle.calls.length > 0));
  assert.ok(await page.evaluate(() => window.__authLifecycle.calls.every(call => call.authorization === `Bearer ${'browser-test-session'}`)));
  await page.close();
});

test('a transient validation failure returns to the login gate and permits retry', async () => {
  const page = await open('validation-failure');
  await page.type('[data-testid="bootstrap-secret"]', 'valid-bootstrap');
  await page.click('[data-testid="connect-session"]');
  await page.waitForSelector('[data-testid="session-error"]');
  assert.match(await page.$eval('[data-testid="session-status"]', node => node.textContent), /SESSION REQUIRED/);
  assert.equal(await page.$('[data-testid="branded-chat-shell"]'), null);

  await page.click('[data-testid="connect-session"]');
  await page.waitForSelector('[data-testid="branded-chat-shell"]');
  await page.close();
});

test('a validated bearer and expiry metadata survive reload without re-bootstrap', async () => {
  const page = await open();
  await connect(page);
  await page.reload({ waitUntil: 'networkidle0' });
  await page.waitForSelector('[data-testid="branded-chat-shell"]');
  const authCalls = await page.evaluate(() => window.__authLifecycle.authCalls);
  assert.equal(authCalls.filter(call => call.method === 'POST').length, 0);
  assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('magistrate.gateway.session')).expires_at), 4102444800);
  await page.close();
});

test('obvious expiry invalidates the session, unmounts protected activity, and returns to login', async () => {
  const page = await open('expiry');
  await connect(page);
  await page.waitForSelector('[data-testid="bootstrap-secret"]', { timeout: 30000 });
  assert.equal(await page.evaluate(() => localStorage.getItem('magistrate.gateway.session')), null);
  await page.close();
});

test('an active protected 401 invalidates once, unmounts protected activity, and returns to login', async () => {
  const page = await open('active-401');
  await connect(page);
  await page.type('[data-testid="captain-prompt"]', 'expire now');
  await page.click('[data-testid="send-captain-prompt"]');
  await page.waitForSelector('[data-testid="bootstrap-secret"]');
  const callsAtLogin = await page.evaluate(() => window.__authLifecycle.calls.length);
  await new Promise(resolve => setTimeout(resolve, 1200));
  assert.equal(await page.evaluate(() => window.__authLifecycle.calls.length), callsAtLogin);
  assert.equal(await page.evaluate(() => localStorage.getItem('magistrate.gateway.session')), null);
  await page.close();
});

test('a 403 remains an authorization error and does not invalidate the session', async () => {
  const page = await open('scope-403');
  await connect(page);
  await page.type('[data-testid="captain-prompt"]', 'needs command scope');
  await page.click('[data-testid="send-captain-prompt"]');
  await page.waitForSelector('[data-testid="captain-send-error"]');
  assert.match(await page.$eval('[data-testid="captain-send-error"]', node => node.textContent), /Missing required scope: command/);
  assert.ok(await page.$('[data-testid="branded-chat-shell"]'));
  assert.notEqual(await page.evaluate(() => localStorage.getItem('magistrate.gateway.session')), null);
  await page.close();
});

test('logout revokes the session locally and returns to the authentication gate', async () => {
  const page = await open();
  await connect(page);
  await page.click('[data-testid="brand-drawer-toggle"]');
  await page.click('[data-testid="settings-open"]');
  await page.click('[data-testid="settings-section-account"]');
  await page.waitForSelector('[data-testid="settings-logout"]');
  await page.click('[data-testid="settings-logout"]');
  await page.waitForSelector('[data-testid="bootstrap-secret"]');
  assert.equal(await page.$('[data-testid="branded-chat-shell"]'), null);
  assert.equal(await page.evaluate(() => localStorage.getItem('magistrate.gateway.session')), null);
  await page.close();
});
