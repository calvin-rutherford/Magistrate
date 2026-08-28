const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const path = require('node:path');
const test = require('node:test');
const puppeteer = require('puppeteer-core');

const PORT = 8091;
const URL = `http://127.0.0.1:${PORT}/chat`;
const terminalOutput = ['tool call that should not be primary', 'terminal line 2'].join('\n');

let server;
let browser;

async function waitForServer() {
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(URL);
      if (response.ok) return;
    } catch {}
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  throw new Error('Expo web server did not become ready');
}

test.before(async () => {
  server = spawn(path.join(process.cwd(), 'node_modules', '.bin', 'expo'), [
    'start', '--web', '--port', String(PORT)
  ], { cwd: process.cwd(), env: { ...process.env, CI: '1' }, stdio: 'ignore' });
  await waitForServer();
  browser = await puppeteer.launch({ executablePath: '/usr/bin/google-chrome', headless: true, args: ['--no-sandbox'] });
});

test.after(async () => {
  await browser?.close();
  server?.kill('SIGTERM');
});

async function openChat(viewport) {
  const page = await browser.newPage();
  await page.setViewport(viewport);
  await page.evaluateOnNewDocument(output => {
    const nativeFetch = window.fetch.bind(window);
    window.__magistrateApiCalls = [];
    window.fetch = (resource, options) => {
      const url = typeof resource === 'string' ? resource : resource.url;
      if (url.includes('/api/v1/captain/output')) {
        window.__magistrateApiCalls.push({ url, method: options?.method, body: options?.body });
        return Promise.resolve(new Response(JSON.stringify({ output }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      if (url.includes('/api/v1/captain/prompt')) {
        window.__magistrateApiCalls.push({ url, method: options?.method, body: options?.body });
        const status = window.__captainPromptStatus || 200;
        return Promise.resolve(new Response(JSON.stringify(status === 200 ? { status: 'submitted', target: 'captain' } : { detail: 'Captain is unavailable' }), { status, headers: { 'Content-Type': 'application/json' } }));
      }
      if (url.includes('/api/v1/execution/capabilities')) {
        return Promise.resolve(new Response(JSON.stringify({
          harnesses: [
            { id: 'codex', label: 'Codex CLI', verified: true, models: [{ id: 'gpt-5', label: 'GPT-5' }] },
            { id: 'reviewer', label: 'Reviewer', verified: true, models: [{ id: 'review-model', label: 'Review Model' }] }
          ], source: 'test', configured: true
        }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      if (url.includes('/api/v1/agents')) return Promise.resolve(new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } }));
      if (url.includes('/api/v1/attention/unified')) return Promise.resolve(new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } }));
      if (url.includes('/api/v1/github/pulls')) return Promise.resolve(new Response(JSON.stringify({ items: [], page: 1, per_page: 20, has_more: false, cached: false }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      if (url.includes('/api/v1/auth/providers')) return Promise.resolve(new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } }));
      if (url.includes('/api/v1/')) return Promise.resolve(new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } }));
      return nativeFetch(resource, options);
    };
  }, terminalOutput);
  await page.goto(URL, { waitUntil: 'networkidle0' });
  await page.waitForSelector('[data-testid="branded-chat-shell"]');
  await page.waitForSelector('[data-testid="harness-select-field"]');
  return page;
}

test('chat opens as the branded conversation shell with raw output behind diagnostics', async () => {
  const page = await openChat({ width: 1100, height: 760 });
  const body = await page.evaluate(() => document.body.innerText);
  assert.match(body, /Firstmate/);
  assert.match(body, /Ask Firstmate anything/);
  assert.doesNotMatch(body, /tool call that should not be primary/);
  await page.$eval('[data-testid="diagnostics-toggle"]', element => element.click());
  await page.waitForSelector('[data-testid="terminal-scroll"]');
  const diagnosticText = await page.$eval('[data-testid="terminal-scroll"]', element => element.textContent);
  assert.match(diagnosticText, /tool call that should not be primary/);
  await page.close();
});

test('drawer starts collapsed and expands sections without replacing chat history', async () => {
  const page = await openChat({ width: 1100, height: 760 });
  assert.equal(await page.$eval('[data-testid="magistrate-drawer"]', element => getComputedStyle(element).opacity), '0');
  await page.click('[data-testid="brand-drawer-toggle"]');
  await page.waitForFunction(() => getComputedStyle(document.querySelector('[data-testid="magistrate-drawer"]')).opacity === '1');
  await page.locator('::-p-text(Attention)').click();
  const body = await page.evaluate(() => document.body.innerText);
  assert.match(body, /Nothing requires your attention/);
  assert.match(body, /Ask Firstmate anything/);
  await page.click('[data-testid="drawer-brand-toggle"]');
  await page.waitForFunction(() => getComputedStyle(document.querySelector('[data-testid="magistrate-drawer"]')).opacity === '0');
  await page.close();
});

test('mobile drawer slides chat aside and keeps composer keyboard usable', async () => {
  const page = await openChat({ width: 390, height: 667, isMobile: true, hasTouch: true });
  const before = await page.$eval('[data-testid="branded-chat-shell"]', element => element.getBoundingClientRect().left);
  await page.click('[data-testid="brand-drawer-toggle"]');
  await page.waitForFunction((previousLeft) => document.querySelector('[data-testid="branded-chat-shell"]').getBoundingClientRect().left > previousLeft + 80, {}, before);
  await page.focus('[data-testid="captain-prompt"]');
  await page.keyboard.type('status please');
  assert.equal(await page.$eval('[data-testid="captain-prompt"]', element => element.value), 'status please');
  await page.keyboard.press('Enter');
  await page.waitForFunction(() => window.__magistrateApiCalls.some(call => call.url.includes('/captain/prompt')));
  assert.equal(JSON.parse((await page.evaluate(() => window.__magistrateApiCalls.find(call => call.url.includes('/captain/prompt')).body))).text, 'status please');
  await page.close();
});

test('execution dropdowns update together and submit verified selection', async () => {
  const page = await openChat({ width: 1100, height: 760 });
  await page.click('[data-testid="harness-select-field"]');
  await page.click('[data-testid="harness-select-option-reviewer"]');
  await page.click('[data-testid="model-select-field"]');
  await page.click('[data-testid="model-select-option-review-model"]');
  await page.focus('[data-testid="captain-prompt"]');
  await page.keyboard.sendCharacter('review the latest output');
  await page.click('[data-testid="send-captain-prompt"]');
  await page.waitForFunction(() => window.__magistrateApiCalls.some(call => call.url.includes('/captain/prompt')));
  const body = JSON.parse(await page.evaluate(() => window.__magistrateApiCalls.find(call => call.url.includes('/captain/prompt')).body));
  assert.equal(body.harness, 'reviewer');
  assert.equal(body.model, 'review-model');
  await page.close();
});
