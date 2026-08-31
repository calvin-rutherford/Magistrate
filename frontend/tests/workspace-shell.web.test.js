const assert = require('node:assert/strict');
const test = require('node:test');
const { launchBrowser, startWebServer } = require('./helpers/web-server');

let server;
let browser;
// Assigned once the dev server has picked a port; the suites read it at call
// time, never at module load.
let BASE;

test.before(async () => {
  server = await startWebServer({ readyPath: '/' });
  BASE = server.base;
  browser = await launchBrowser();
});

test.after(async () => {
  await browser?.close();
  await server?.stop();
});

async function open(url) {
  const page = await browser.newPage();
  await page.evaluateOnNewDocument(() => {
    const nativeFetch = window.fetch.bind(window);
    window.fetch = (resource, options) => {
      const requestUrl = typeof resource === 'string' ? resource : resource.url;
      if (requestUrl.includes('/api/v1/auth/session')) {
        const payload = options?.method === 'POST' ? { session_token: 'browser-test-session', token_type: 'Bearer', expires_at: 4102444800, scopes: ['read', 'account', 'providers', 'notifications', 'voice', 'command'], user_id: 'default_user' } : { authenticated: true, expires_at: 4102444800, scopes: ['read', 'account', 'providers', 'notifications', 'voice', 'command'], user_id: 'default_user' };
        return Promise.resolve(new Response(JSON.stringify(payload), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      if (requestUrl.includes('/agents')) return Promise.resolve(new Response(JSON.stringify([{ id: 'w1:p7', name: 'Live agent', status: 'working', harness: 'codex' }]), { status: 200 }));
      if (requestUrl.includes('/attention/unified')) return Promise.resolve(new Response('[]', { status: 200 }));
      if (requestUrl.includes('/github/pulls')) return Promise.resolve(new Response(JSON.stringify({ items: [], page: 1, per_page: 20, has_more: false, cached: false }), { status: 200 }));
      if (requestUrl.includes('/health')) return Promise.resolve(new Response(JSON.stringify({ status: 'healthy', service: 'gateway', herdr_socket_connected: true }), { status: 200 }));
      if (requestUrl.includes('/captain/output')) return Promise.resolve(new Response(JSON.stringify({ output: 'live terminal output' }), { status: 200 }));
      if (requestUrl.includes('/execution/capabilities')) return Promise.resolve(new Response(JSON.stringify({ harnesses: [], source: 'test', configured: false }), { status: 200 }));
      return nativeFetch(resource, options);
    };
  });
  await page.goto(url, { waitUntil: 'networkidle0' });
  return page;
}

test('root and explicit home resolve to the standalone chat shell', async () => {
  for (const route of ['/', '/home']) {
    const page = await open(BASE + route);
    await page.waitForSelector('[data-testid="branded-chat-shell"]');
    assert.equal(await page.$('[data-testid="workspace-shell"]'), null);
    assert.ok(await page.$('[data-testid="captain-prompt"]'));
    await page.close();
  }
});

test('chat is a standalone route and preserves agent target deep links', async () => {
  const page = await open(`${BASE}/chat?agentId=w1%3Ap7`);
  await page.waitForSelector('[data-testid="branded-chat-shell"]');
  assert.equal(await page.$('[data-testid="workspace-shell"]'), null);
  assert.equal(await page.$eval('[data-testid="captain-prompt"]', node => node.getAttribute('placeholder')), 'Message Magi');
  assert.match(await page.$eval('[data-testid="captain-prompt"]', node => node.getAttribute('aria-label')), /w1:p7/);
  assert.equal(new URL(page.url()).pathname, '/chat');
  await page.close();
});
