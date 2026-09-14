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
    const state = { mode, valid: false, calls: [], authCalls: [], userId: sessionStorage.getItem('__auth_test_user') || 'default_user', token: 'browser-test-session', profileName: '' };
    const expiresAt = mode === 'expiry' ? Math.floor(Date.now() / 1000) + 20 : 4102444800;
    let validationFailures = mode === 'validation-failure' ? 1 : 0;
    window.__authLifecycle = state;
    const json = (payload, status = 200) => Promise.resolve(new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } }));
    window.fetch = (resource, options = {}) => {
      const url = typeof resource === 'string' ? resource : resource.url;
      const method = options.method || 'GET';
      if (url.includes('/api/v1/auth/friend-beta/session')) {
        state.authCalls.push({ url, method, body: options.body || null });
        let body = {};
        try { body = JSON.parse(options.body || '{}'); } catch {}
        if (body.access_code !== `mgb_${'A'.repeat(43)}`) return json({ detail: 'Invalid or expired Friend Beta access code' }, 401);
        state.valid = true;
        state.userId = 'friend-beta-user';
        state.token = 'friend-beta-session';
        return json({ session_token: state.token, token_type: 'Bearer', expires_at: expiresAt, scopes: ['read', 'account', 'notifications'], user_id: state.userId, auth_method: 'friend-beta-access', renewable_until: 4102444800, onboarding_required: !state.profileName });
      }
      if (url.includes('/api/v1/auth/session')) {
        state.authCalls.push({ url, method, body: options.body || null });
        if (method === 'POST') {
          let body = {};
          try { body = JSON.parse(options.body || '{}'); } catch {}
          if (body.bootstrap_secret !== 'valid-bootstrap') return json({ detail: 'Invalid session bootstrap credential' }, 401);
          state.valid = true;
          state.token = 'browser-test-session';
          return json({ session_token: state.token, token_type: 'Bearer', expires_at: expiresAt, scopes: ['read', 'account', 'providers', 'notifications', 'voice', 'command'], user_id: 'default_user' });
        }
        const authorization = options.headers?.Authorization || options.headers?.get?.('Authorization');
        if (validationFailures > 0) {
          validationFailures -= 1;
          return json({ detail: 'Transient session validation failure' }, 503);
        }
        if (authorization === `Bearer ${state.token}`) {
          state.valid = true;
          const friend = state.userId === 'friend-beta-user';
          return json({ authenticated: true, expires_at: expiresAt, scopes: friend ? ['read', 'account', 'notifications'] : ['read', 'account', 'providers', 'notifications', 'voice', 'command'], user_id: state.userId, auth_method: friend ? 'friend-beta-access' : 'operator-bootstrap', onboarding_required: friend && !state.profileName });
        }
        return json({ detail: 'Invalid or expired session' }, 401);
      }
      if (url.includes('/api/v1/')) {
        const authorization = options.headers?.Authorization || options.headers?.get?.('Authorization');
        state.calls.push({ url, method, authorization: authorization || null });
        if (!state.valid || authorization !== `Bearer ${state.token}`) return json({ detail: 'Authentication required' }, 401);
        if (mode === 'active-401' && url.includes('/magi/messages')) return json({ detail: 'Invalid or expired session' }, 401);
        if (mode === 'scope-403' && url.includes('/magi/messages')) return json({ detail: 'Missing required scope: command' }, 403);
        const nativeMessages = () => state.turn ? [
          { id: 'mgm_auth_user_0001', conversation_id: 'mgc_auth_native_0001', turn_id: 'mgt_auth_native_0001', client_message_id: state.turn.clientMessageId, reply_to_message_id: null, role: 'user', content: state.turn.text, status: 'completed', source: 'text', sequence_index: 0, revision: 1, attachments: [], created_at: 1756000000000, updated_at: 1756000000000 },
          { id: 'mgm_auth_assistant_0001', conversation_id: 'mgc_auth_native_0001', turn_id: 'mgt_auth_native_0001', client_message_id: null, reply_to_message_id: 'mgm_auth_user_0001', role: 'assistant', content: 'Authenticated reply from Magi.', status: 'completed', source: 'magi-native', sequence_index: 1, revision: 1, attachments: [], created_at: 1756000000001, updated_at: 1756000000001 },
        ] : [];
        const nativeEnvelope = messages => ({ schema_version: 'magi.native-chat.v1', conversation: { id: 'mgc_auth_native_0001', created_at: 1756000000000, updated_at: 1756000000001 }, conversation_id: 'mgc_auth_native_0001', messages, has_more: false, next_before: null, latest_change: messages.length });
        if (url.includes('/magi/messages') && method === 'POST') {
          let body = {};
          try { body = JSON.parse(options.body || '{}'); } catch {}
          state.turn = { clientMessageId: body.client_message_id, text: body.content };
          const messages = nativeMessages();
          const envelope = nativeEnvelope(messages);
          return json({ ...envelope, status: 'completed', user_message: messages[0], assistant_message: messages[1], duplicate: false, retry: false, attempt: 1 });
        }
        if (url.includes('/magi/conversations/current')) return json(nativeEnvelope(nativeMessages()));
        if (url.includes('/activity/snapshot')) return json({ schema_version: 'activity.v1', records: [], focus_records: [], focus_truncated: false, snapshot_cursor: 0, latest_sequence: 0, next_before: null, has_more: false, summary: { active_objectives: 0, operation_count: 0, pending_decisions: 0 }, reconciliation: 'persisted-only', sources: [] });
        if (url.includes('/activity')) return json({ schema_version: 'activity.v1', records: [], next_cursor: 0, latest_cursor: 0, has_more: false, summary: { active_objectives: 0, operation_count: 0, pending_decisions: 0 }, reconciliation: 'persisted-only', sources: [] });
        if (url.includes('/execution/capabilities')) return json({ harnesses: [], profiles: [], source: 'test', configured: false });
        if (url.includes('/execution/settings')) return json({ profile_id: null, switching_behavior: 'migrate', unavailable_behavior: 'error', migration_supported: false, credentials: [] });
        if (url.includes('/account/profile') && method === 'POST') {
          state.profileName = typeof options.body?.get === 'function' ? String(options.body.get('name') || '') : '';
          return json({ user_id: state.userId, name: state.profileName, email: '', avatar_url: '', bio: '' });
        }
        if (url.includes('/account/profile')) return json({ user_id: state.userId, name: state.profileName, email: '', avatar_url: '', bio: '' });
        if (url.includes('/notifications/events')) return json({ events: [] });
        if (url.includes('/recent-activity')) return json({ items: [], sources: { firstmate: 'available', github: 'available' } });
        if (url.includes('/auth/providers')) return json([]);
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

async function seedPrincipalCache(page, principal = 'default_user') {
  await page.evaluate(owner => {
    const row = { id: 'mgm_secret_0001', serverId: 'mgm_secret_0001', conversationId: 'mgc_secret_0001', clientMessageId: null, replyToServerId: 'mgm_secret_user_0001', serverStatus: 'completed', role: 'assistant', text: `private for ${owner}`, source: 'text', sentAt: Date.now(), progress: 'complete', revision: 1, turnId: 'mgt_secret_0001', sequenceIndex: 0, attachments: [] };
    localStorage.setItem(`magistrate.magi.messages.v1.${encodeURIComponent(owner)}`, JSON.stringify({ schema_version: 'magi-conversation-cache.v1', principal_id: owner, messages: { 'mgm_secret_0001': row } }));
    localStorage.setItem(`magistrate.magi.pending.v1.${encodeURIComponent(owner)}`, JSON.stringify({ schema_version: 'magi-conversation-pending.v1', principal_id: owner, messages: {} }));
    localStorage.setItem(`magistrate.activity.canonical.v1.${encodeURIComponent(owner)}`, JSON.stringify({
      schema_version: 'activity-cache.v1', principal: owner, cursor: 1, summary_cursor: 1,
      summary_authoritative: true,
      summary: { active_objectives: 1, operation_count: 0, pending_decisions: 1 },
      records: [{
        id: 'ca-private-decision', sequence: 1, delivery_sequence: 1, revision: 1,
        kind: 'decision.requested', state: 'awaiting-user', importance: 'attention',
        title: `Private decision for ${owner}`, summary: 'Choose a release channel.', summary_truncated: false,
        task_id: 'release-task', decision_key: 'release-channel', objective_id: 'obj-release',
        run_id: 'run-release', project: 'Magistrate', occurred_at: null, observed_at: Date.now(),
        refs: [], source: { instance_id: 'firstmate:main', event_id: null },
      }],
    }));
  }, principal);
}

async function principalCacheKeys(page, principal = 'default_user') {
  return page.evaluate(owner => Object.keys(localStorage).filter(key =>
    key === `magistrate.magi.messages.v1.${encodeURIComponent(owner)}`
      || key === `magistrate.magi.pending.v1.${encodeURIComponent(owner)}`
      || key === `magistrate.activity.canonical.v1.${encodeURIComponent(owner)}`), principal);
}

async function connect(page) {
  await page.waitForSelector('[data-testid="bootstrap-secret"]');
  await page.type('[data-testid="bootstrap-secret"]', 'valid-bootstrap');
  await page.click('[data-testid="connect-session"]');
  await page.waitForSelector('[data-testid="branded-chat-shell"]');
  await page.waitForSelector('[data-testid="magi-prompt"]');
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
  await page.type('[data-testid="magi-prompt"]', 'status please');
  await page.click('[data-testid="send-magi-prompt"]');
  await page.waitForFunction(() => window.__authLifecycle.calls.some(call => call.url.includes('/magi/messages')));
  await page.waitForFunction(() => document.body.innerText.includes('Authenticated reply from Magi.'));
  assert.ok(await page.evaluate(() => window.__authLifecycle.calls.length > 0));
  assert.ok(await page.evaluate(() => window.__authLifecycle.calls.every(call => call.authorization === `Bearer ${'browser-test-session'}`)));
  await page.close();
});

test('a Friend Beta access code creates its own principal and requires profile onboarding before mounting Chat', async () => {
  const page = await open('friend');
  const accessCode = `mgb_${'A'.repeat(43)}`;
  await page.waitForSelector('[data-testid="bootstrap-secret"]');
  await page.type('[data-testid="bootstrap-secret"]', accessCode);
  await page.click('[data-testid="connect-session"]');

  await page.waitForSelector('[data-testid="friend-beta-onboarding-title"]');
  assert.equal(await page.$('[data-testid="branded-chat-shell"]'), null);
  await page.type('[data-testid="friend-beta-display-name"]', 'Ada Friend');
  await page.click('[data-testid="friend-beta-complete-onboarding"]');
  await page.waitForSelector('[data-testid="branded-chat-shell"]');

  const evidence = await page.evaluate(() => ({
    authCalls: window.__authLifecycle.authCalls,
    calls: window.__authLifecycle.calls,
    stored: JSON.parse(localStorage.getItem('magistrate.gateway.session')),
    displayName: localStorage.getItem('magistrate.account.display-name'),
  }));
  const exchange = evidence.authCalls.find(call => call.url.includes('/auth/friend-beta/session'));
  assert.equal(JSON.parse(exchange.body).access_code, accessCode);
  assert.equal(evidence.stored.user_id, 'friend-beta-user');
  assert.equal(evidence.stored.renewal_code, undefined, 'web must not persist the long-lived Friend Beta code');
  assert.equal(evidence.displayName, 'Ada Friend');
  assert.ok(evidence.calls.some(call => call.url.includes('/account/profile') && call.method === 'POST'));
  assert.ok(evidence.calls.every(call => call.authorization === 'Bearer friend-beta-session'));
  await page.close();
});

test('web restore rejects a persisted Friend Beta renewal credential', async () => {
  const page = await open();
  const accessCode = `mgb_${'B'.repeat(43)}`;
  await page.waitForSelector('[data-testid="bootstrap-secret"]');
  await page.evaluate(code => localStorage.setItem('magistrate.gateway.session', JSON.stringify({
    token: 'browser-test-session', expires_at: 4102444800,
    scopes: ['read', 'account', 'notifications'], user_id: 'friend-beta-user',
    auth_method: 'friend-beta-access', renewal_code: code,
    renewable_until: 4102444800,
  })), accessCode);
  await page.reload({ waitUntil: 'networkidle0' });
  await page.waitForSelector('[data-testid="bootstrap-secret"]');
  assert.equal(await page.evaluate(() => localStorage.getItem('magistrate.gateway.session')), null);
  assert.equal(await page.evaluate(() => window.__authLifecycle.authCalls.some(call => call.url.includes('/auth/friend-beta/session'))), false);
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

test('server-observed principal change evicts the previous principal cache before protected remount', async () => {
  const page = await open();
  await connect(page);
  await seedPrincipalCache(page);
  assert.ok((await principalCacheKeys(page)).length >= 2);
  await page.evaluate(() => sessionStorage.setItem('__auth_test_user', 'other_user'));
  await page.reload({ waitUntil: 'networkidle0' });
  await page.waitForSelector('[data-testid="branded-chat-shell"]');
  assert.deepEqual(await principalCacheKeys(page, 'default_user'), []);
  const storedSession = await page.evaluate(() => JSON.parse(localStorage.getItem('magistrate.gateway.session')));
  assert.equal(storedSession.user_id, 'other_user');
  await page.close();
});

test('obvious expiry invalidates the session, evicts principal chat caches, and returns to login', async () => {
  const page = await open('expiry');
  await connect(page);
  await seedPrincipalCache(page);
  await page.waitForSelector('[data-testid="bootstrap-secret"]', { timeout: 30000 });
  assert.equal(await page.evaluate(() => localStorage.getItem('magistrate.gateway.session')), null);
  assert.deepEqual(await principalCacheKeys(page), []);
  await page.close();
});

test('an active protected 401 invalidates once, evicts principal chat caches, and returns to login', async () => {
  const page = await open('active-401');
  await connect(page);
  await seedPrincipalCache(page);
  await page.type('[data-testid="magi-prompt"]', 'expire now');
  await page.click('[data-testid="send-magi-prompt"]');
  await page.waitForSelector('[data-testid="bootstrap-secret"]');
  const callsAtLogin = await page.evaluate(() => window.__authLifecycle.calls.length);
  await new Promise(resolve => setTimeout(resolve, 1200));
  assert.equal(await page.evaluate(() => window.__authLifecycle.calls.length), callsAtLogin);
  assert.equal(await page.evaluate(() => localStorage.getItem('magistrate.gateway.session')), null);
  assert.deepEqual(await principalCacheKeys(page), []);
  await page.close();
});

test('a 403 remains an authorization error and does not invalidate the session', async () => {
  const page = await open('scope-403');
  await connect(page);
  await page.type('[data-testid="magi-prompt"]', 'needs command scope');
  await page.click('[data-testid="send-magi-prompt"]');
  await page.waitForSelector('[data-testid="magi-send-error"]');
  assert.match(await page.$eval('[data-testid="magi-send-error"]', node => node.textContent), /Missing required scope: command/);
  assert.ok(await page.$('[data-testid="branded-chat-shell"]'));
  assert.notEqual(await page.evaluate(() => localStorage.getItem('magistrate.gateway.session')), null);
  await page.close();
});

test('logout revokes the session, evicts principal chat caches, and returns to the authentication gate', async () => {
  const page = await open();
  await connect(page);
  await seedPrincipalCache(page);
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
  assert.deepEqual(await principalCacheKeys(page), []);
  await page.close();
});
