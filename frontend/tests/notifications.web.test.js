const assert = require('node:assert/strict');
const test = require('node:test');
const { launchBrowser, startWebServer } = require('./helpers/web-server');

const events = [
  { id: 'question-1', provider: 'firstmate', title: 'Deployment choice', subtitle: 'Choose the rollout window.', priority: 'HIGH', status: 'needs-decision', project: 'Magistrate', target_id: 'deploy-1', context: { task_id: 'task-1', decision_key: 'deploy-1' }, action: { schema_version: 'attention-action.v1', action_key: 'aa1_attention_question_1', decision_key: 'deploy-1', target: { provider: 'firstmate', task_id: 'task-1', decision_key: 'deploy-1' }, allowed_actions: ['approve', 'reject'], confirmation_required: true, consequence: 'Records the choice against the exact Firstmate decision.', reversible: true, status: 'available' }, url: '/attention?item=question-1', deep_link: '/attention?item=question-1', requires_action: true, notification_kind: 'captain_question', revision: '1' },
  { id: 'pr-2', provider: 'github', title: 'Review ready', subtitle: 'A pull request needs your review.', priority: 'MEDIUM', status: 'ready', url: '/pr-detail?number=2', deep_link: '/pr-detail?number=2', requires_action: true, notification_kind: 'pr_ready', revision: '1' }
];

let server;
let browser;
// Assigned once the dev server has picked a port; the suites read it at call
// time, never at module load.
let BASE;

test.before(async () => {
  server = await startWebServer({ readyPath: '/chat' });
  BASE = server.base;
  browser = await launchBrowser();
});

test.after(async () => {
  await browser?.close();
  await server?.stop();
});

async function openApp({ notificationMode, eventBatch = [], attention = events, url = '/chat' } = {}) {
  const page = await browser.newPage();
  await page.evaluateOnNewDocument(({ mode, batch, attentionItems }) => {
    window.__notificationEvents = [...batch];
    window.__notificationCalls = [];
    window.__attentionActionCalls = [];
    window.__browserNotifications = [];
    const nativeFetch = window.fetch.bind(window);
    window.fetch = (resource, options) => {
      const requestUrl = typeof resource === 'string' ? resource : resource.url;
      if (requestUrl.includes('/api/v1/auth/session')) {
        const payload = options?.method === 'POST' ? { session_token: 'browser-test-session', token_type: 'Bearer', expires_at: 4102444800, scopes: ['read', 'account', 'providers', 'notifications', 'voice', 'command'], user_id: 'default_user' } : { authenticated: true, expires_at: 4102444800, scopes: ['read', 'account', 'providers', 'notifications', 'voice', 'command'], user_id: 'default_user' };
        return Promise.resolve(new Response(JSON.stringify(payload), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      if (requestUrl.includes('/api/v1/notifications/events/delivered')) {
        window.__notificationCalls.push({ url: requestUrl, method: options?.method, body: options?.body });
        return Promise.resolve(new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      if (requestUrl.includes('/api/v1/notifications/events/ack')) {
        window.__notificationCalls.push({ url: requestUrl, method: options?.method, body: options?.body });
        return Promise.resolve(new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      if (requestUrl.includes('/api/v1/notifications/events')) {
        window.__notificationCalls.push({ url: requestUrl, method: options?.method });
        const next = window.__notificationEvents.shift() || [];
        return Promise.resolve(new Response(JSON.stringify({ events: next, unread: next }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      if (requestUrl.includes('/api/v1/attention/actions/by-item/')) {
        const action = attentionItems.find(item => item.action)?.action;
        return Promise.resolve(new Response(JSON.stringify(action || { status: 'unsupported' }), { status: action ? 200 : 404, headers: { 'Content-Type': 'application/json' } }));
      }
      if (requestUrl.includes('/api/v1/attention/actions/') && requestUrl.endsWith('/prepare')) {
        window.__attentionActionCalls.push({ url: requestUrl, method: options?.method, body: options?.body });
        const requestBody = JSON.parse(options?.body || '{}');
        return Promise.resolve(new Response(JSON.stringify({ schema_version: 'attention-action.v1', status: 'confirmation_required', action_key: 'aa1_attention_question_1', action: requestBody.action || 'approve', decision_key: 'deploy-1', target: { provider: 'firstmate', task_id: 'task-1', decision_key: 'deploy-1' }, consequence: 'Records the choice against the exact Firstmate decision.', reversible: true, confirmation_token: 'confirmation-token-123456', expires_at: 4102444800 }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      if (requestUrl.includes('/api/v1/attention/actions/') && requestUrl.endsWith('/execute')) {
        window.__attentionActionCalls.push({ url: requestUrl, method: options?.method, body: options?.body });
        return Promise.resolve(new Response(JSON.stringify({ schema_version: 'attention-action.v1', action_key: 'aa1_attention_question_1', action: 'approve', decision_key: 'deploy-1', target: { provider: 'firstmate', task_id: 'task-1', decision_key: 'deploy-1' }, status: 'succeeded', evidence: { provider: 'firstmate', decision_key: 'deploy-1', target_id: 'task-1', operation: 'captain-hold-answer', recorded: true }, timestamp: 1700000000 }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      if (requestUrl.includes('/api/v1/attention/unified')) return Promise.resolve(new Response(JSON.stringify(attentionItems), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      if (requestUrl.includes('/api/v1/agents')) return Promise.resolve(new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } }));
      if (requestUrl.includes('/api/v1/github/pulls/2')) return Promise.resolve(new Response(JSON.stringify({ id: 2, number: 2, title: 'Exact PR metadata', repository: 'acme/ship', author: 'captain', branch: 'fix/attention', state: 'OPEN', is_draft: false, review_status: 'REVIEW_REQUIRED', checks: { status: 'PASSING', passed: 3, failed: 0, pending: 0, summary: '3 passed, 0 failed' }, mergeable: 'MERGEABLE', summary: 'PR summary', body: 'Full PR context', reviews: [], created_at: '2026-08-30T10:00:00Z', updated_at: '2026-08-30T11:00:00Z', merged_at: null, requires_attention: true, url: 'https://github.com/acme/ship/pull/2' }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      if (requestUrl.includes('/api/v1/github/pulls')) return Promise.resolve(new Response(JSON.stringify({ items: [], page: 1, per_page: 20, has_more: false, cached: false }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      if (requestUrl.includes('/api/v1/health')) return Promise.resolve(new Response(JSON.stringify({ status: 'healthy', service: 'test', herdr_socket_connected: true }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      if (requestUrl.includes('/api/v1/recent-activity')) return Promise.resolve(new Response(JSON.stringify({ items: [], sources: { firstmate: 'available', github: 'available' } }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      if (requestUrl.includes('/api/v1/auth/providers')) return Promise.resolve(new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } }));
      if (requestUrl.includes('/api/v1/execution/capabilities')) return Promise.resolve(new Response(JSON.stringify({ harnesses: [], profiles: [], source: 'test', configured: false }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      if (requestUrl.includes('/api/v1/execution/settings')) return Promise.resolve(new Response(JSON.stringify({ profile_id: null, switching_behavior: 'migrate', unavailable_behavior: 'error', migration_supported: false }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      if (requestUrl.includes('/api/v1/voice/capabilities')) return Promise.resolve(new Response(JSON.stringify({ modes: [], configured: false }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      if (requestUrl.includes('/api/v1/agents/captain/history')) return Promise.resolve(new Response(JSON.stringify({ target: 'captain', messages: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      return nativeFetch(resource, options);
    };
    if (mode === 'granted' || mode === 'denied') {
      class FakeNotification {
        static permission = mode;
        static requestPermission = async () => mode;
        constructor(title, options) { window.__browserNotifications.push({ title, body: options.body, data: options.data }); }
        close() {}
      }
      Object.defineProperty(window, 'Notification', { configurable: true, value: FakeNotification });
    } else Object.defineProperty(window, 'Notification', { configurable: true, value: undefined });
  }, { mode: notificationMode, batch: eventBatch, attentionItems: attention });
  await page.goto(`${BASE}${url}`, { waitUntil: 'networkidle0' });
  return page;
}

test('new attention transition uses one real browser notification, an unread dot, and no in-app popup', async () => {
  const page = await openApp({ notificationMode: 'granted', eventBatch: [[events[0]]] });
  await page.waitForFunction(() => window.__browserNotifications.length === 1);
  assert.equal(await page.evaluate(() => window.__browserNotifications[0].title), 'Your answer is needed');
  await page.waitForSelector('[data-testid="unread-attention-dot"]');
  assert.equal(await page.$('[data-testid^="notification-"]'), null);
  await page.waitForSelector('[data-testid="brand-drawer-toggle"]');
  assert.match(await page.$eval('[data-testid="brand-drawer-toggle"]', node => node.getAttribute('aria-label')), /1 unread captain attention item/);
  assert.ok(await page.evaluate(() => window.__notificationCalls.some(call => call.url.includes('/events/delivered'))));
  await page.close();
});

test('denied or unsupported browser notifications retain a quiet unread drawer fallback without a popup', async () => {
  for (const notificationMode of ['denied', 'unsupported']) {
    const page = await openApp({ notificationMode, eventBatch: [[events[0], events[1]]] });
    await page.waitForSelector('[data-testid="unread-attention-dot"]');
    assert.equal(await page.evaluate(() => window.__browserNotifications.length), 0);
    assert.equal(await page.$('[data-testid^="notification-"]'), null);
    await page.close();
  }
});

test('a PR Attention target preserves exact in-app detail routing and metadata', async () => {
  const page = await openApp({ notificationMode: 'unsupported', url: '/attention', attention: [events[1]], eventBatch: [[]] });
  await page.waitForSelector('[data-testid="attention-item-pr-2"]');
  await page.click('[data-testid="attention-item-pr-2"]');
  await page.waitForFunction(() => location.pathname === '/pr-detail' && document.body.innerText.includes('Full PR context'));
  assert.match(await page.evaluate(() => document.body.innerText), /acme\/ship · #2|fix\/attention|3 passed, 0 failed/);
  await page.close();
});

test('Attention approve/reject requires explicit confirmation and displays bounded evidence', async () => {
  const page = await openApp({ notificationMode: 'unsupported', url: '/attention', eventBatch: [[]] });
  await page.click('[data-testid="attention-item-question-1"]');
  await page.click('[data-testid="attention-reject-question-1"]');
  await page.waitForSelector('[data-testid="attention-confirmation"]');
  assert.match(await page.$eval('[data-testid="attention-confirmation"]', node => node.textContent), /REJECT|task-1|Consequence|Reversible: Yes/);
  assert.equal(await page.evaluate(() => window.__attentionActionCalls.length), 1, 'prepare is not execution');
  await page.click('[data-testid="attention-action-cancel"]');
  assert.equal(await page.evaluate(() => window.__attentionActionCalls.length), 1, 'cancel never executes');
  await page.click('[data-testid="attention-approve-question-1"]');
  await page.waitForSelector('[data-testid="attention-confirmation"]');
  await page.click('[data-testid="attention-action-confirm"]');
  await page.waitForSelector('[data-testid="attention-action-evidence"]');
  assert.equal(await page.evaluate(() => window.__attentionActionCalls.length), 3, 'only prepare and one execute are sent');
  assert.match(await page.$eval('[data-testid="attention-action-evidence"]', node => node.textContent), /SUCCEEDED|deploy-1|task-1/);
  await page.close();
});

test('selecting an Attention item opens its detailed view and acknowledges only that item', async () => {
  const page = await openApp({ notificationMode: 'unsupported', url: '/attention', eventBatch: [[]] });
  await page.waitForSelector('[data-testid="attention-item-question-1"]');
  await page.click('[data-testid="attention-item-question-1"]');
  await page.waitForSelector('[data-testid="attention-detail-question-1"]');
  assert.match(await page.$eval('[data-testid="attention-detail-question-1"]', node => node.textContent), /Pick|Choose the rollout window|Deployment choice/);
  assert.equal(await page.$('[data-testid="floating-bottom-controls"]'), null, 'detailed attention views must not have floating chat, voice, or gesture controls');
  await page.waitForFunction(() => window.__notificationCalls.some(call => call.url.includes('/events/ack')));
  await page.close();
});
