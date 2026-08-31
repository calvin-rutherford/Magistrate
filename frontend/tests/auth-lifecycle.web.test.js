const assert = require('node:assert/strict');
const test = require('node:test');
const { launchBrowser, startWebServer } = require('./helpers/web-server');

let server;
let browser;
// Assigned once the dev server has picked a port; the suites read it at call
// time, never at module load.
let URL;

test.before(async () => {
  server = await startWebServer({ readyPath: '/chat' });
  URL = `${server.base}/chat`;
  browser = await launchBrowser();
});

test.after(async () => {
  await browser?.close();
  await server?.stop();
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
        // The captain transcript is the gateway's canonical record, so a prompt
        // answers with the turn it recorded rather than a bare reply string.
        const canonicalMessages = () => state.turn ? [
          { id: 'cm_0_u', turn_id: 'ct_0', client_message_id: state.turn.clientMessageId, role: 'user', type: 'conversation', text: state.turn.text, visible_in_chat: true, sequence_index: 0, revision: 1, turn_status: 'answered' },
          { id: 'cm_0_a', turn_id: 'ct_0', role: 'assistant', type: 'conversation', text: 'Authenticated reply from Firstmate.', visible_in_chat: true, sequence_index: 999, revision: 1, turn_status: 'answered' },
        ] : [];
        if (url.includes('/captain/prompt')) {
          let body = {};
          try { body = JSON.parse(options.body || '{}'); } catch {}
          state.turn = { clientMessageId: body.message_id, text: body.text };
          return json({ status: 'submitted', target: 'captain', message_id: body.message_id, conversation: { schema_version: 'conversation.v1', target: 'captain', turn_id: 'ct_0', messages: canonicalMessages() } });
        }
        if (url.includes('/conversations/') && url.includes('/messages')) return json({ schema_version: 'conversation.v1', target: 'captain', messages: canonicalMessages() });
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
  // The drawer slides in and Settings is a long scroller, so wait for the layer
  // to arrive and bring Sign out into view before pressing it.
  await page.click('[data-testid="brand-drawer-toggle"]');
  await page.waitForFunction(() => Number(getComputedStyle(document.querySelector('[data-testid="magistrate-drawer"]')).opacity) > 0.95);
  await page.click('[data-testid="settings-open"]');
  await page.waitForFunction(() => Number(getComputedStyle(document.querySelector('[data-testid="settings-sheet"]')).opacity) > 0.95);
  await page.click('[data-testid="settings-section-account"]');
  await page.waitForSelector('[data-testid="settings-logout"]');
  await page.$eval('[data-testid="settings-logout"]', element => element.scrollIntoView({ block: 'center' }));
  await page.click('[data-testid="settings-logout"]');
  await page.waitForSelector('[data-testid="bootstrap-secret"]');
  assert.equal(await page.$('[data-testid="branded-chat-shell"]'), null);
  assert.equal(await page.evaluate(() => localStorage.getItem('magistrate.gateway.session')), null);
  await page.close();
});
