const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const path = require('node:path');
const test = require('node:test');
const puppeteer = require('puppeteer-core');

const PORT = 8094;
const BASE = `http://127.0.0.1:${PORT}/diagnostics`;
let server;
let browser;

async function waitForServer() {
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    try {
      if ((await fetch(BASE)).ok) return;
    } catch {}
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  throw new Error('Expo web server did not become ready');
}

test.before(async () => {
  server = spawn(path.join(process.cwd(), 'node_modules', '.bin', 'expo'), ['start', '--web', '--port', String(PORT)], {
    cwd: process.cwd(),
    env: { ...process.env, CI: '1' },
    stdio: 'ignore'
  });
  await waitForServer();
  browser = await puppeteer.launch({ executablePath: '/usr/bin/google-chrome', headless: true, args: ['--no-sandbox'] });
});

test.after(async () => {
  await browser?.close();
  server?.kill('SIGTERM');
});

async function openHome({ agentsStatus = 200, agents = null, attention = [] } = {}) {
  const page = await browser.newPage();
  await page.evaluateOnNewDocument(({ agentsStatus, agents, attention }) => {
    const agent = {
      id: 'w1:p7',
      name: 'Live captain',
      harness: 'codex',
      status: 'working',
      pane_id: 'w1:p7',
      tab_id: 'w1:t7',
      workspace_id: 'w1'
    };
    const pr = {
      id: 42,
      number: 42,
      title: 'Real pull request',
      repository: 'acme/ship',
      author: 'captain',
      branch: 'fix/nav',
      state: 'OPEN',
      is_draft: false,
      mergeable: 'MERGEABLE',
      review_status: 'REVIEW_REQUIRED',
      checks: { status: 'PASSING', passed: 2, failed: 0, pending: 0, summary: '2 passed, 0 failed' },
      reviews: [],
      created_at: '2026-08-26T10:00:00Z',
      updated_at: '2026-08-26T11:00:00Z',
      merged_at: null,
      summary: 'Summary',
      body: 'Body',
      requires_attention: true,
      url: 'https://github.com/acme/ship/pull/42'
    };
    window.fetch = resource => {
      const requestUrl = typeof resource === 'string' ? resource : resource.url;
      if (requestUrl.includes('/agents')) {
        return Promise.resolve(new Response(JSON.stringify(agentsStatus === 200 ? (agents || [agent]) : { detail: 'Agent service unavailable' }), {
          status: agentsStatus,
          headers: { 'Content-Type': 'application/json' }
        }));
      }
      if (requestUrl.includes('/health')) {
        return Promise.resolve(new Response(JSON.stringify({ status: 'healthy', service: 'magistrate-gateway', herdr_socket_connected: true }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' }
        }));
      }
      if (requestUrl.includes('/github/pulls')) {
        return Promise.resolve(new Response(JSON.stringify({ items: [pr], page: 1, per_page: 20, has_more: false, cached: false }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' }
        }));
      }
      if (requestUrl.includes('/attention/unified')) {
        return Promise.resolve(new Response(JSON.stringify(attention), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      return Promise.resolve(new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } }));
    };
  }, { agentsStatus, agents, attention });
  await page.goto(BASE, { waitUntil: 'networkidle0' });
  return page;
}

test('Home renders live agent identity and makes the agent card actionable', async () => {
  const page = await openHome();
  await page.waitForFunction(() => document.body.innerText.includes('AGENT FLEET (1)'));
  const body = await page.evaluate(() => document.body.innerText);
  assert.match(body, /Live captain/);
  assert.match(body, /Harness: codex/);
  assert.match(body, /1 active · 0 idle/);
  assert.doesNotMatch(body, /Firstmate Autonomous Control Loop|Claude 3\.7 Sonnet/);

  await page.locator('::-p-text(Live captain)').click();
  await page.waitForFunction(() => location.pathname === '/chat' && document.body.innerText.includes('Live captain'));
  assert.match(await page.$eval('[data-testid="chat-target"]', node => node.textContent), /Live captain|w1:p7/);
  await page.close();
});

test('Home derives live status counts and orders active before idle deterministically', async () => {
  const page = await openHome({ agents: [
    { id: 'idle-z', name: 'Zulu', status: 'paused', harness: 'codex' },
    { id: 'active-b', name: 'Bravo', status: 'working', harness: 'codex' },
    { id: 'active-a', name: 'Alpha', status: 'running', harness: 'codex' },
    { id: 'unknown', name: 'Mystery', status: 'mystery', harness: 'codex' }
  ] });
  await page.waitForFunction(() => document.body.innerText.includes('2 active · 1 idle · 1 unavailable'));
  const cards = await page.$$('[data-testid^="agent-card-"]');
  const names = await Promise.all(cards.map(card => card.$eval('[data-testid^="agent-name-"]', node => node.textContent).catch(() => '')));
  assert.deepEqual(names, ['Alpha', 'Bravo', 'Zulu', 'Mystery']);
  assert.match(await page.$eval('[data-testid="agent-card-unknown"]', element => element.textContent), /UNAVAILABLE/);
  await page.close();
});

test('Home reports an agent error instead of inventing an empty or active state', async () => {
  const page = await openHome({ agentsStatus: 503 });
  await page.waitForFunction(() => document.body.innerText.includes('Agent service unavailable'));
  const body = await page.evaluate(() => document.body.innerText);
  assert.match(body, /Agent service unavailable/);
  assert.match(body, /AGENT FLEET \(0\)/);
  assert.doesNotMatch(body, /Firstmate Autonomous Control Loop|Claude 3\.7 Sonnet/);
  await page.close();
});

test('Home shows live attention items and dismisses them through the notification contract', async () => {
  const page = await openHome({ attention: [{ id: 'question-1', provider: 'firstmate', title: 'Choose a rollout', subtitle: 'A decision is waiting.', status: 'needs-decision', url: '/attention?item=question-1', requires_action: true }] });
  await page.waitForSelector('[data-testid="attention-item-question-1"]');
  await page.click('[data-testid="attention-dismiss-question-1"]');
  await page.waitForFunction(() => !document.querySelector('[data-testid="attention-item-question-1"]'));
  await page.close();
});
