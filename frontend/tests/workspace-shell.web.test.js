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

async function openWorkspace(url = BASE, viewport = { width: 1100, height: 760 }) {
  const page = await browser.newPage();
  await page.setViewport(viewport);
  await page.evaluateOnNewDocument(() => {
    const agent = { id: 'w1:p7', name: 'Live agent', harness: 'codex', status: 'working', pane_id: 'w1:p7' };
    const pr = { id: 42, number: 42, title: 'Live pull request', repository: 'acme/ship', author: 'captain', branch: 'fix/nav', state: 'OPEN', review_status: 'REVIEW_REQUIRED', checks: { summary: '2 passed' }, requires_attention: true, url: 'https://github.com/acme/ship/pull/42' };
    window.fetch = resource => {
      const requestUrl = typeof resource === 'string' ? resource : resource.url;
      let body = {};
      if (requestUrl.includes('/captain/output')) body = { output: 'live terminal output' };
      else if (requestUrl.includes('/execution/capabilities')) body = { harnesses: [{ id: 'codex', label: 'Codex', verified: true, models: [{ id: 'gpt-5', label: 'GPT-5' }] }], source: 'test', configured: true };
      else if (requestUrl.includes('/attention/unified')) body = [{ id: 'attention-1', provider: 'firstmate', title: 'Answer needed', subtitle: 'A live question', status: 'ready', url: '/attention?item=attention-1', requires_action: true }];
      else if (requestUrl.includes('/github/pulls/42')) body = pr;
      else if (requestUrl.includes('/github/pulls')) body = { items: [pr], page: 1, per_page: 20, has_more: false, cached: false };
      else if (requestUrl.includes('/agents')) body = [agent];
      else if (requestUrl.includes('/health')) body = { status: 'healthy', service: 'gateway', herdr_socket_connected: true, herdr_version: 'test' };
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    };
  });
  await page.goto(url, { waitUntil: 'networkidle0' });
  await page.waitForSelector('[data-testid="workspace-shell"]');
  await page.waitForSelector('[data-testid="terminal-scroll"]');
  return page;
}

test('Chat remains mounted while a rail destination opens as an in-shell panel', async () => {
  const page = await openWorkspace();
  assert.ok(await page.$('[data-testid="workspace-rail"]'));
  assert.equal(await page.$('[data-testid="workspace-panel"]'), null);
  await page.click('[data-testid="rail-fleet"]');
  await page.waitForSelector('[data-testid="workspace-panel"]');
  assert.equal(await page.$('[data-testid="chat-canvas"]') !== null, true);
  assert.match(await page.url(), /section=fleet/);
  assert.match(await page.$eval('[data-testid="panel-title"]', node => node.textContent), /Agent fleet/);
  await page.click('[data-testid="panel-close"]');
  await page.waitForFunction(() => !document.querySelector('[data-testid="workspace-panel"]'));
  assert.equal(new URL(page.url()).search, '');
  await page.close();
});

test('legacy agent deep links translate to shell state and agent cards target Chat', async () => {
  const page = await openWorkspace(`${BASE}/agents?agentId=w1%3Ap7`);
  await page.waitForSelector('[data-testid="workspace-panel"]');
  assert.equal(await page.$('[data-testid="chat-canvas"]') !== null, true);
  assert.ok(await page.$('[data-testid="agent-card-w1:p7"]'));
  await page.click('[data-testid="agent-card-w1:p7"]');
  await page.waitForFunction(() => !document.querySelector('[data-testid="workspace-panel"]'));
  assert.match(await page.$eval('[data-testid="chat-target"]', node => node.textContent), /w1:p7/);
  assert.match(new URL(page.url()).search, /agentId=w1%3Ap7|agentId=w1%3Ap7/i);
  await page.close();
});

test('desktop rail collapses and mobile navigation is a drawer', async () => {
  const desktop = await openWorkspace();
  const expanded = await desktop.$eval('[data-testid="workspace-rail"]', node => node.getBoundingClientRect().width);
  await desktop.click('[data-testid="rail-toggle"]');
  const collapsed = await desktop.$eval('[data-testid="workspace-rail"]', node => node.getBoundingClientRect().width);
  assert.notEqual(collapsed, expanded);
  await desktop.close();

  const mobile = await openWorkspace(BASE, { width: 390, height: 667, isMobile: true, hasTouch: true });
  assert.equal(await mobile.$('[data-testid="workspace-rail"]'), null);
  await mobile.click('[data-testid="mobile-rail-toggle"]');
  await mobile.waitForSelector('[data-testid="workspace-rail"]');
  await mobile.click('[data-testid="rail-attention"]');
  await mobile.waitForSelector('[data-testid="workspace-panel"]');
  assert.match(await mobile.$eval('[data-testid="panel-title"]', node => node.textContent), /Needs attention/);
  await mobile.close();
});

test('invalid panel query is ignored and browser Back returns to Chat', async () => {
  const page = await openWorkspace(`${BASE}/?section=not-a-destination`);
  assert.equal(await page.$('[data-testid="workspace-panel"]'), null);
  await page.click('[data-testid="rail-attention"]');
  await page.waitForSelector('[data-testid="workspace-panel"]');
  await page.goBack({ waitUntil: 'networkidle0' });
  await page.waitForFunction(() => !document.querySelector('[data-testid="workspace-panel"]'));
  assert.equal(new globalThis.URL(page.url()).search, '');
  await page.close();
});
