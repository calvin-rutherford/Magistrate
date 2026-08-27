const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const path = require('node:path');
const test = require('node:test');
const puppeteer = require('puppeteer-core');

const PORT = 8096;
const BASE = `http://127.0.0.1:${PORT}`;
let server;
let browser;

async function waitForServer() {
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    try { if ((await fetch(BASE)).ok) return; } catch {}
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  throw new Error('Expo web server did not become ready');
}

test.before(async () => {
  server = spawn(path.join(process.cwd(), 'node_modules', '.bin', 'expo'), ['start', '--web', '--port', String(PORT)], { cwd: process.cwd(), env: { ...process.env, CI: '1' }, stdio: 'ignore' });
  await waitForServer();
  browser = await puppeteer.launch({ executablePath: '/usr/bin/google-chrome', headless: true, args: ['--no-sandbox'] });
});

test.after(async () => { await browser?.close(); server?.kill('SIGTERM'); });

async function open(url) {
  const page = await browser.newPage();
  await page.evaluateOnNewDocument(() => {
    const nativeFetch = window.fetch.bind(window);
    window.fetch = (resource, options) => {
      const requestUrl = typeof resource === 'string' ? resource : resource.url;
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

test('root and explicit home are standalone homepage routes', async () => {
  for (const route of ['/', '/home']) {
    const page = await open(BASE + route);
    await page.waitForFunction(() => document.body.innerText.includes('AGENT FLEET'));
    assert.equal(await page.$('[data-testid="workspace-shell"]'), null);
    assert.equal(await page.$('[data-testid="chat-canvas"]'), null);
    await page.close();
  }
});

test('chat is a standalone route and preserves agent target deep links', async () => {
  const page = await open(`${BASE}/chat?agentId=w1%3Ap7`);
  await page.waitForSelector('[data-testid="chat-canvas"]');
  assert.equal(await page.$('[data-testid="workspace-shell"]'), null);
  assert.match(await page.$eval('[data-testid="chat-target"]', node => node.textContent), /w1:p7/);
  assert.equal(new URL(page.url()).pathname, '/chat');
  await page.close();
});
