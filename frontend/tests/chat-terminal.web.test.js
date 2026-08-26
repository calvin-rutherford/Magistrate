const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const path = require('node:path');
const test = require('node:test');
const puppeteer = require('puppeteer-core');

const PORT = 8091;
const ORIGIN = `http://127.0.0.1:${PORT}`;
const URL = `${ORIGIN}/chat`;
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
      if (url.includes('/api/v1/agents/') && url.includes('/output')) {
        window.__magistrateApiCalls.push({ url, method: options?.method, body: options?.body });
        window.__terminalPolls += 1;
        return Promise.resolve(new Response(JSON.stringify({ output: window.__terminalOutput }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' }
        }));
      }
      if (url.endsWith('/api/v1/agents')) {
        return Promise.resolve(new Response(JSON.stringify([
          { id: 'w1:p2', pane_id: 'w1:p2', workspace_id: 'w1', tab_id: 'w1:t2', name: 'firstmate', terminal_title: 'firstmate', harness: 'codex', status: 'working' },
          { id: 'w1:p7', pane_id: 'w1:p7', workspace_id: 'w1', tab_id: 'w1:t7', name: 'reviewer', terminal_title: 'Magistrate', harness: 'codex', status: 'idle' }
        ]), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      if (url.includes('/api/v1/github/pulls')) {
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
  await page.goto(ORIGIN + (viewport.path || '/chat'), { waitUntil: 'networkidle0' });
  await page.waitForSelector('[data-testid="terminal-scroll"]');
  await page.waitForFunction(selector => {
    const element = document.querySelector(selector);
    return element && element.scrollHeight > element.clientHeight && element.scrollTop > 0;
  }, {}, '[data-testid="terminal-scroll"]');
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

  const box = await terminal.boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.wheel({ deltaY: -500 });
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
    await page.evaluate(() => new URL(window.__magistrateApiCalls.find(call => call.url.includes('/output'))?.url || location.href).searchParams.get('lines')),
    '4294967295'
  );
  await page.$eval('[data-testid="terminal-scroll"]', element => { element.scrollTop = 0; });
  await page.waitForFunction(() => document.body.innerText.includes('terminal line 1'));
  assert.equal(await page.evaluate(() => (document.body.innerText.match(/terminal line 1\n/g) || []).length), 1);
  await page.waitForFunction(() => window.__terminalPolls >= 2, { timeout: 5_000 });
  assert.equal(await page.evaluate(() => (document.body.innerText.match(/terminal line 1\n/g) || []).length), 1);
  await page.close();
});

test('pane tabs switch the exact Herdr target and honor a deep-linked pane', async () => {
  const page = await openChat({ width: 1100, height: 760, path: '/chat?pane=w1%3Ap7' });
  assert.equal(await page.$eval('[data-testid="pane-tab-w1:p7"]', element => element.getAttribute('aria-selected')), 'true');
  assert.ok(await page.evaluate(() => window.__magistrateApiCalls.some(call => call.url.includes('/agents/w1%3Ap7/output'))));

  await page.click('[data-testid="pane-tab-w1:p2"]');
  await page.waitForFunction(() => new URL(location.href).searchParams.get('pane') === 'w1:p2');
  await page.waitForFunction(() => window.__magistrateApiCalls.some(call => call.url.includes('/agents/w1%3Ap2/output')));
  assert.equal(await page.$eval('[data-testid="pane-tab-w1:p2"]', element => element.textContent), 'Firstmate');
  await page.close();
});

test('Home agent links deep-link Chat to the exact pane', async () => {
  const page = await openChat({ width: 1100, height: 760 });
  await page.goto(`${ORIGIN}/`, { waitUntil: 'networkidle0' });
  await page.click('[aria-label="Open reviewer pane in Chat"]');
  await page.waitForFunction(() => location.pathname === '/chat' && new URL(location.href).searchParams.get('pane') === 'w1:p7');
  await page.close();
});

test('terminal controls expose only the captain set and send exact keys to the selected pane', async () => {
  const page = await openChat({ width: 1100, height: 760, path: '/chat?pane=w1%3Ap7' });
  const controls = await page.$$eval('[data-testid^="terminal-control-"]', elements => elements.map(element => element.textContent));
  assert.deepEqual(controls, ['Enter', '↑', '↓', 'Yes', 'No']);

  for (const [testId, encodedKey] of [
    ['terminal-control-enter', 'Enter'],
    ['terminal-control-up', 'Up'],
    ['terminal-control-down', 'Down'],
    ['terminal-control-y', 'y'],
    ['terminal-control-n', 'n'],
  ]) {
    await page.click(`[data-testid="${testId}"]`);
    await page.waitForFunction(
      (key) => window.__magistrateApiCalls.some(call => call.url.includes(`/agents/w1%3Ap7/send-key?key=${key}`)),
      {}, encodeURIComponent(encodedKey)
    );
  }
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
  await terminal.focus();
  await page.keyboard.press('PageUp');
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
  const beforeTouch = await terminal.evaluate(element => element.scrollTop);
  const box = await terminal.boundingBox();
  const client = await page.createCDPSession();
  await client.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: box.x + 30, y: box.y + 40 }] });
  await client.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: box.x + 30, y: box.y + 140 }] });
  await client.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  await page.waitForFunction(
    (selector, previousTop) => document.querySelector(selector).scrollTop < previousTop,
    {}, '[data-testid="terminal-scroll"]', beforeTouch
  );

  await page.click('[data-testid="captain-prompt"]');
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
