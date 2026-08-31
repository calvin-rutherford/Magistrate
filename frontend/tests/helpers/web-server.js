// Shared lifecycle for the puppeteer web suites.
//
// Every suite needs the same three things: an Expo web dev server nobody else is
// using, a headless Chrome pointed at it, and a teardown that actually waits for
// the server to die before the next suite starts its own Metro. Doing that in
// one place keeps the suites runnable in any order, in parallel, and from any
// git worktree.
const { spawn } = require('node:child_process');
const net = require('node:net');
const path = require('node:path');
const puppeteer = require('puppeteer-core');

const READY_TIMEOUT_MS = Number(process.env.MAGISTRATE_WEB_TEST_READY_TIMEOUT_MS) || 180_000;
const CHROME_PATH = process.env.PUPPETEER_EXECUTABLE_PATH || '/usr/bin/google-chrome';

// An OS-assigned port is the only allocation that cannot silently collide with a
// parallel suite, another git worktree, or a leftover dev server -- a fixed port
// that is already taken makes Expo serve *someone else's* bundle, which the
// readiness probe cannot tell apart from our own.
function reserveFreePort() {
  return new Promise((resolve, reject) => {
    const probe = net.createServer();
    probe.unref();
    probe.once('error', reject);
    probe.listen(0, '127.0.0.1', () => {
      const { port } = probe.address();
      probe.close(() => resolve(port));
    });
  });
}

function stopServer(child) {
  if (!child || child.exitCode !== null || child.signalCode !== null) return Promise.resolve();
  return new Promise(resolve => {
    const kill = setTimeout(() => child.kill('SIGKILL'), 5_000);
    kill.unref();
    child.once('exit', () => { clearTimeout(kill); resolve(); });
    child.kill('SIGTERM');
  });
}

/**
 * Starts `expo start --web` on a port of its own and resolves once it answers.
 * `readyPath` is the route the probe requests, so a suite can wait on the page
 * it is about to drive rather than only on the server socket.
 */
async function startWebServer({ readyPath = '/' } = {}) {
  const port = Number(process.env.MAGISTRATE_WEB_TEST_PORT) || await reserveFreePort();
  const base = `http://127.0.0.1:${port}`;
  const child = spawn(
    path.join(process.cwd(), 'node_modules', '.bin', 'expo'),
    ['start', '--web', '--port', String(port)],
    { cwd: process.cwd(), env: { ...process.env, CI: '1', BROWSER: 'none' }, stdio: ['ignore', 'pipe', 'pipe'] },
  );
  let output = '';
  const collect = chunk => { output = (output + chunk).slice(-4000); };
  child.stdout.on('data', collect);
  child.stderr.on('data', collect);
  let exit = null;
  child.once('exit', (code, signal) => { exit = { code, signal }; });

  const deadline = Date.now() + READY_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (exit) throw new Error(`Expo web server exited before becoming ready (code ${exit.code}, signal ${exit.signal}):\n${output}`);
    try {
      if ((await fetch(`${base}${readyPath}`, { signal: AbortSignal.timeout(10_000) })).ok) {
        return { port, base, stop: () => stopServer(child) };
      }
    } catch {}
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  await stopServer(child);
  throw new Error(`Expo web server on ${base}${readyPath} did not become ready within ${READY_TIMEOUT_MS}ms:\n${output}`);
}

function launchBrowser({ args = [] } = {}) {
  return puppeteer.launch({ executablePath: CHROME_PATH, headless: true, args: ['--no-sandbox', ...args] });
}

/**
 * Clicks the first *rendered* element matching a puppeteer selector.
 *
 * Expo Router keeps a collapsed (0x0) copy of a screen mounted alongside the
 * visible one, so `page.locator(selector).click()` can bind to the hidden copy
 * and then wait 30s for a box that never appears. Filtering by bounding box
 * keeps the real mouse click on the element a user would press.
 */
async function clickRendered(page, selector) {
  const handles = await page.$$(selector);
  for (const handle of handles) {
    const box = await handle.boundingBox();
    if (box && box.width > 0 && box.height > 0) {
      await handle.click();
      return;
    }
  }
  throw new Error(`No rendered element matched ${selector} (${handles.length} collapsed match(es))`);
}

module.exports = { startWebServer, launchBrowser, clickRendered, CHROME_PATH, READY_TIMEOUT_MS };
