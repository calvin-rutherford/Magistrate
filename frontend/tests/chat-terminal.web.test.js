const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const path = require('node:path');
const test = require('node:test');
const puppeteer = require('puppeteer-core');

const PORT = 8091;
const URL = `http://127.0.0.1:${PORT}/chat`;
const terminalOutput = Array.from({ length: 180 }, (_, index) => `terminal line ${index + 1}`).join('\n');

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
    window.fetch = (resource, options) => {
      const url = typeof resource === 'string' ? resource : resource.url;
      if (url.includes('/api/v1/captain/output')) {
        return Promise.resolve(new Response(JSON.stringify({ output }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' }
        }));
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
  await page.waitForFunction(
    expected => document.body.innerText.includes(expected),
    {},
    'terminal line 180'
  );
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

test('terminal remains usable on a phone-sized viewport and composer accepts keyboard input', async () => {
  const page = await openChat({ width: 390, height: 667, isMobile: true, hasTouch: true });
  const layout = await page.$eval('[data-testid="terminal-scroll"]', element => ({
    height: element.clientHeight,
    bottom: element.getBoundingClientRect().bottom
  }));
  assert.ok(layout.height >= 100, 'responsive layout must preserve a usable terminal viewport');
  assert.ok(layout.bottom <= 667, 'terminal must remain inside the viewport');

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
