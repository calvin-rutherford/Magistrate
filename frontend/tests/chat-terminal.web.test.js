const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const path = require('node:path');
const test = require('node:test');
const puppeteer = require('puppeteer-core');

const PORT = 8091;
const URL = `http://127.0.0.1:${PORT}/chat`;
const HISTORY_LINES = 1_000;
const terminalOutput = Array.from({ length: HISTORY_LINES }, (_, index) => `terminal line ${index + 1}`).join('\n');

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
  browser = await puppeteer.launch({
    executablePath: '/usr/bin/google-chrome',
    headless: true,
    args: ['--no-sandbox']
  });
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
    window.__terminalOutput = output;
    window.__terminalPolls = 0;
    window.fetch = (resource, options) => {
      const url = typeof resource === 'string' ? resource : resource.url;
      if (url.includes('/api/v1/captain/output')) {
        window.__magistrateApiCalls.push({ url, method: options?.method, body: options?.body });
        window.__terminalPolls += 1;
        return Promise.resolve(new Response(JSON.stringify({ output: window.__terminalOutput }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' }
        }));
      }
      if (url.includes('/api/v1/captain/prompt')) {
        window.__magistrateApiCalls.push({ url, method: options?.method, body: options?.body });
        const status = window.__captainPromptStatus || 200;
        const body = status === 200 ? { status: 'submitted', target: 'captain' } : { detail: 'Captain is unavailable' };
        return Promise.resolve(new Response(JSON.stringify(body), {
          status,
          headers: { 'Content-Type': 'application/json' }
        }));
      }
      if (url.includes('/api/v1/execution/capabilities')) {
        return Promise.resolve(new Response(JSON.stringify({
          harnesses: [
            { id: 'codex', label: 'Codex CLI', verified: true, models: [{ id: 'gpt-5', label: 'GPT-5' }, { id: 'gpt-5-mini', label: 'GPT-5 Mini' }] },
            { id: 'reviewer', label: 'Reviewer', verified: true, models: [{ id: 'review-model', label: 'Review Model' }] }
          ],
          source: 'test',
          configured: true
        }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      if (url.includes('/api/v1/agents')) {
        return Promise.resolve(new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      if (url.includes('/api/v1/')) {
        window.__magistrateApiCalls.push({ url, method: options?.method, body: options?.body });
        return Promise.resolve(new Response('{}', {
          status: 200,
          headers: { 'Content-Type': 'application/json' }
        }));
      }
      return nativeFetch(resource, options);
    };
  }, terminalOutput);
  await page.goto(URL, { waitUntil: 'networkidle0' });
  await page.waitForSelector('[data-testid="terminal-scroll"]');
  await page.waitForFunction(selector => {
    const element = document.querySelector(selector);
    return element && element.scrollHeight > element.clientHeight && element.scrollTop > 0;
  }, {}, '[data-testid="terminal-scroll"]');
  await page.waitForSelector('[data-testid="harness-select-field"]');
  return page;
}

test('terminal has a bounded viewport and responds to wheel scrolling', async () => {
  const page = await openChat({ width: 1100, height: 760 });
  const terminal = await page.$('[data-testid="terminal-scroll"]');
  const initial = await terminal.evaluate(element => ({
    clientHeight: element.clientHeight,
    scrollHeight: element.scrollHeight,
    scrollTop: element.scrollTop
  }));

  assert.ok(initial.clientHeight > 0);
  assert.ok(initial.scrollHeight > initial.clientHeight, 'terminal output must overflow its own viewport');
  assert.ok(initial.scrollTop > 0, 'new output should initially follow the bottom');

  await terminal.evaluate(element => {
    element.dispatchEvent(new WheelEvent('wheel', { deltaY: -500, bubbles: true, cancelable: true }));
  });
  await page.waitForFunction(
    (selector, previousTop) => document.querySelector(selector).scrollTop < previousTop,
    {},
    '[data-testid="terminal-scroll"]',
    initial.scrollTop
  );
  await page.close();
});

test('history beyond the old limit remains reachable without duplicate polling output', async () => {
  const page = await openChat({ width: 1100, height: 760 });
  assert.equal(
    await page.evaluate(() => new URL(window.__magistrateApiCalls.find(call => call.url.includes('/captain/output'))?.url || location.href).searchParams.get('lines')),
    '4294967295'
  );
  await page.$eval('[data-testid="terminal-scroll"]', element => { element.scrollTop = 0; });
  await page.waitForFunction(() => document.body.innerText.includes('terminal line 1'));
  assert.equal(await page.evaluate(() => (document.body.innerText.match(/terminal line 1\n/g) || []).length), 1);
  await page.waitForFunction(() => window.__terminalPolls >= 2, { timeout: 5_000 });
  assert.equal(await page.evaluate(() => (document.body.innerText.match(/terminal line 1\n/g) || []).length), 1);
  await page.close();
});

test('polling preserves an older viewport and jump-to-latest resumes following', async () => {
  const page = await openChat({ width: 1100, height: 760 });
  const terminal = await page.$('[data-testid="terminal-scroll"]');
  await terminal.evaluate(element => { element.scrollTop = Math.floor(element.scrollHeight / 2); });
  await page.waitForSelector('[data-testid="jump-to-latest"]');
  const before = await terminal.evaluate(element => element.scrollTop);

  await page.evaluate(line => { window.__terminalOutput += `\nterminal line ${line}`; }, HISTORY_LINES + 1);
  await page.waitForFunction(() => document.body.innerText.includes('NEW MESSAGES'), { timeout: 5_000 });
  const after = await terminal.evaluate(element => element.scrollTop);
  assert.ok(Math.abs(after - before) < 20, `older viewport moved from ${before} to ${after}`);

  await page.click('[data-testid="jump-to-latest"]');
  await page.waitForFunction(selector => {
    const element = document.querySelector(selector);
    return element.scrollHeight - element.clientHeight - element.scrollTop < 40;
  }, {}, '[data-testid="terminal-scroll"]');
  await page.evaluate(line => { window.__terminalOutput += `\nterminal line ${line}`; }, HISTORY_LINES + 2);
  await page.waitForFunction(selector => {
    const element = document.querySelector(selector);
    return element.scrollHeight - element.clientHeight - element.scrollTop < 40;
  }, { timeout: 5_000 }, '[data-testid="terminal-scroll"]');
  await page.close();
});

test('focused terminal supports keyboard page scrolling', async () => {
  const page = await openChat({ width: 1100, height: 760 });
  const terminal = await page.$('[data-testid="terminal-scroll"]');
  const before = await terminal.evaluate(element => element.scrollTop);
  await terminal.evaluate(element => {
    element.focus();
    element.dispatchEvent(new KeyboardEvent('keydown', { key: 'PageUp', bubbles: true, cancelable: true }));
  });
  await page.waitForFunction(
    (selector, previousTop) => document.querySelector(selector).scrollTop < previousTop,
    {}, '[data-testid="terminal-scroll"]', before
  );
  await page.close();
});

test('terminal remains usable on a phone-sized viewport and composer accepts keyboard input', async () => {
  const page = await openChat({ width: 390, height: 667, isMobile: true, hasTouch: true });
  const layout = await page.$eval('[data-testid="terminal-scroll"]', element => ({
    height: element.clientHeight,
    bottom: element.getBoundingClientRect().bottom
  }));
  assert.ok(layout.height >= 100, 'responsive layout must preserve a usable terminal viewport');
  assert.ok(layout.bottom <= 667, 'terminal must remain inside the viewport');

  const terminal = await page.$('[data-testid="terminal-scroll"]');
  const beforeTouch = await terminal.evaluate(element => {
    element.scrollTop = element.scrollHeight;
    return element.scrollTop;
  });
  await terminal.evaluate(element => {
    const start = new Touch({ identifier: 1, target: element, clientX: 30, clientY: 40, pageX: 30, pageY: 40 });
    const end = new Touch({ identifier: 1, target: element, clientX: 30, clientY: 140, pageX: 30, pageY: 140 });
    element.dispatchEvent(new TouchEvent('touchstart', { touches: [start], bubbles: true }));
    element.dispatchEvent(new TouchEvent('touchmove', { touches: [end], bubbles: true }));
    element.dispatchEvent(new TouchEvent('touchend', { touches: [], bubbles: true }));
  });
  await page.waitForFunction(
    (selector, previousTop) => document.querySelector(selector).scrollTop < previousTop,
    {}, '[data-testid="terminal-scroll"]', beforeTouch
  );

  await page.focus('[data-testid="captain-prompt"]');
  await page.keyboard.type('status please');
  assert.equal(await page.$eval('[data-testid="captain-prompt"]', element => element.value), 'status please');
  assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('data-testid')), 'captain-prompt');
  await page.keyboard.press('Enter');
  await page.waitForFunction(() => window.__magistrateApiCalls.some(call => call.url.includes('/captain/prompt')));
  const promptCall = await page.evaluate(() => window.__magistrateApiCalls.find(call => call.url.includes('/captain/prompt')));
  assert.equal(promptCall.method, 'POST');
  assert.equal(JSON.parse(promptCall.body).text, 'status please');
  await page.close();
});

test('composer exposes a send action and restores a failed prompt for retry', async () => {
  const page = await openChat({ width: 390, height: 667, isMobile: true, hasTouch: true });
  await page.focus('[data-testid="captain-prompt"]');
  await page.keyboard.type('send this command');
  assert.equal(await page.$eval('[data-testid="send-captain-prompt"]', element => element.textContent), 'SEND');
  await page.click('[data-testid="send-captain-prompt"]');
  await page.waitForFunction(() => window.__magistrateApiCalls.some(call => call.url.includes('/captain/prompt')));
  assert.equal(await page.$eval('[data-testid="captain-prompt"]', element => element.value), '');
  await page.waitForFunction(() => document.querySelector('[data-testid="send-captain-prompt"]')?.textContent === 'SEND');

  await page.evaluate(() => { window.__captainPromptStatus = 503; });
  await page.focus('[data-testid="captain-prompt"]');
  await page.keyboard.type('retry this command');
  await page.click('[data-testid="send-captain-prompt"]');
  await page.waitForSelector('[data-testid="captain-send-error"]');
  assert.equal(await page.$eval('[data-testid="captain-prompt"]', element => element.value), 'retry this command');
  assert.match(await page.$eval('[data-testid="captain-send-error"]', element => element.textContent), /Captain is unavailable/);
  await page.close();
});

test('execution dropdowns update together and submit the verified selection', async () => {
  const page = await openChat({ width: 1100, height: 760 });
  await page.waitForFunction(() => document.body.innerText.includes('Codex CLI'));
  await page.click('[data-testid="harness-select-field"]');
  await page.click('[data-testid="harness-select-option-reviewer"]');
  await page.click('[data-testid="model-select-field"]');
  await page.click('[data-testid="model-select-option-review-model"]');
  await page.waitForFunction(() => document.querySelector('[data-testid="model-select-field"]')?.textContent?.includes('Review Model'));
  await page.focus('[data-testid="captain-prompt"]');
  await page.keyboard.sendCharacter('review the latest output');
  assert.equal(await page.$eval('[data-testid="captain-prompt"]', element => element.value), 'review the latest output');
  await page.click('[data-testid="send-captain-prompt"]');
  await page.waitForFunction(() => window.__magistrateApiCalls.some(call => call.url.includes('/captain/prompt')) || document.querySelector('[data-testid="captain-send-error"]'));
  assert.equal(await page.$('[data-testid="captain-send-error"]'), null);
  const promptCall = await page.evaluate(() => window.__magistrateApiCalls.find(call => call.url.includes('/captain/prompt')));
  const body = JSON.parse(promptCall.body);
  assert.equal(body.harness, 'reviewer');
  assert.equal(body.model, 'review-model');
  await page.close();
});

test('workspace rail replaces route-button navigation and opens an in-shell destination', async () => {
  const page = await openChat({ width: 390, height: 667, isMobile: true, hasTouch: true });
  assert.equal(await page.$('[data-testid="chat-nav-home"]'), null);
  await page.click('[data-testid="mobile-rail-toggle"]');
  await page.waitForSelector('[data-testid="workspace-rail"]');
  await page.click('[data-testid="rail-fleet"]');
  await page.waitForSelector('[data-testid="workspace-panel"]');
  assert.match(new globalThis.URL(page.url()).search, /section=fleet/);
  assert.ok(await page.$('[data-testid="terminal-scroll"]'));
  await page.close();
});
