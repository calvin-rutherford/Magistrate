const assert = require('node:assert/strict');
const test = require('node:test');
const { clickRendered, launchBrowser, startWebServer } = require('./helpers/web-server');

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

async function pageWithGitHubData(url = BASE, externalUrl = 'https://github.com/acme/ship/pull/42', activityItems, suppliedAppStatus) {
  const page = await browser.newPage();
  await page.evaluateOnNewDocument((githubUrl, suppliedActivity, appStatusOverride) => {
    const pr = { id: 42, number: 42, title: 'Real pull request', repository: 'acme/ship', repository_id: 4200, author: 'captain', branch: 'fix/nav', state: 'OPEN', is_draft: false, mergeable: 'MERGEABLE', review_status: 'REVIEW_REQUIRED', checks: { status: 'PASSING', passed: 2, failed: 0, pending: 0, summary: '2 passed, 0 failed' }, reviews: [], created_at: '2026-08-26T10:00:00Z', updated_at: '2026-08-26T11:00:00Z', merged_at: null, summary: 'Summary', body: 'Authoritative body', requires_attention: true, url: githubUrl };
    const activity = suppliedActivity || [
      { id: 'github:pull:42:merged', type: 'pull_request_merged', title: 'Real pull request', description: 'PR #42 merged', occurred_at: '2026-08-28T12:00:00Z', source: 'github', project: 'acme/ship', url: githubUrl, pull_request_number: 42 },
      { id: 'firstmate:task:done', type: 'task_completed', title: 'Completed fleet task', description: 'Completed task', occurred_at: '2026-08-28T10:00:00Z', source: 'firstmate', project: 'Magistrate', url: null, pull_request_number: null },
      { id: 'firstmate:task:requested', type: 'task_requested', title: 'Captain request', description: 'Task requested', occurred_at: '2026-08-28T09:00:00Z', source: 'firstmate', project: 'Magistrate', url: null, pull_request_number: null },
    ];
    window.fetch = (resource, options) => {
      const requestUrl = typeof resource === 'string' ? resource : resource.url;
      if (requestUrl.includes('/api/v1/auth/session')) {
        const payload = options?.method === 'POST' ? { session_token: 'browser-test-session', token_type: 'Bearer', expires_at: 4102444800, scopes: ['read', 'account', 'providers', 'notifications', 'voice', 'command'], user_id: 'default_user' } : { authenticated: true, expires_at: 4102444800, scopes: ['read', 'account', 'providers', 'notifications', 'voice', 'command'], user_id: 'default_user' };
        return Promise.resolve(new Response(JSON.stringify(payload), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      const appStatus = appStatusOverride || { schema_version: 'github-app-readiness.v1', status: 'configured', configured: true, app_slug: 'magistrate', required_permissions: { contents: 'read', pull_requests: 'read', checks: 'read', metadata: 'read' }, repository_selection: 'selected_repositories_recommended', installation_tokens: 'server-only', installations: [{ installation_id: 1, account_login: 'acme', account_type: 'Organization', repository_selection: 'selected', status: 'active', last_reconciled_at: 1 }], repository_count: 1 };
      const body = requestUrl.includes('/github/app/status') ? appStatus : requestUrl.includes('/github/pulls/42') ? pr : requestUrl.includes('/recent-activity') ? { items: activity, sources: { firstmate: 'available', github: 'available' } } : requestUrl.includes('/github/pulls') ? { items: [pr], page: 1, per_page: 20, has_more: false, cached: false } : [];
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    };
  }, externalUrl, activityItems, suppliedAppStatus);
  await page.goto(url, { waitUntil: 'networkidle0' });
  return page;
}

test('Recent Activity opens an in-app PR detail before GitHub', async () => {
  const page = await pageWithGitHubData();
  await page.click('[data-testid="brand-drawer-toggle"]');
  await page.waitForFunction(() => Number(getComputedStyle(document.querySelector('[data-testid="magistrate-drawer"]')).opacity) > 0.95);
  await page.evaluate(() => document.querySelector('[data-testid="drawer-section-activity"]').click());
  await page.waitForFunction(() => document.body.innerText.includes('Real pull request'));
  await page.locator('::-p-text(Real pull request)').click();
  await page.waitForFunction(() => location.pathname.includes('pr-detail') && document.body.innerText.includes('Authoritative body'));
  assert.match(await page.evaluate(() => document.body.innerText), /2 passed, 0 failed/);
  await page.close();
});

test('Recent Activity renders general real events newest first', async () => {
  const page = await pageWithGitHubData();
  await page.click('[data-testid="brand-drawer-toggle"]');
  await page.waitForFunction(() => Number(getComputedStyle(document.querySelector('[data-testid="magistrate-drawer"]')).opacity) > 0.95);
  await page.evaluate(() => document.querySelector('[data-testid="drawer-section-activity"]').click());
  await page.waitForFunction(() => document.body.innerText.includes('Captain request'));
  const text = await page.evaluate(() => document.querySelector('[data-testid="drawer-panel-activity"]').innerText);
  assert.ok(text.indexOf('Real pull request') < text.indexOf('Completed fleet task'));
  assert.ok(text.indexOf('Completed fleet task') < text.indexOf('Captain request'));
  assert.match(text, /PR #42 merged/);
  assert.match(text, /Completed task/);
  assert.match(text, /Task requested/);
  await page.close();
});

test('Recent Activity has a truthful empty state', async () => {
  const page = await pageWithGitHubData(BASE, undefined, []);
  await page.click('[data-testid="brand-drawer-toggle"]');
  await page.waitForFunction(() => Number(getComputedStyle(document.querySelector('[data-testid="magistrate-drawer"]')).opacity) > 0.95);
  await page.evaluate(() => document.querySelector('[data-testid="drawer-section-activity"]').click());
  await page.waitForFunction(() => document.body.innerText.includes('No recent activity is available.'));
  assert.doesNotMatch(await page.evaluate(() => document.querySelector('[data-testid="drawer-panel-activity"]').innerText), /PR #|task/i);
  await page.close();
});

test('repository list routes pull details with repository identity', async () => {
  const page = await pageWithGitHubData(`${BASE}/prs`);
  await page.waitForFunction(() => document.body.innerText.includes('Real pull request'));
  await clickRendered(page, '::-p-text(DETAILS →)');
  await page.waitForFunction(() => location.pathname.includes('pr-detail'));
  const query = await page.evaluate(() => location.search);
  assert.match(query, /number=42/);
  assert.match(query, /repositoryId=4200/);
  await page.close();
});

test('repository UI reports external activation block without offering a fake install', async () => {
  const blocked = { schema_version: 'github-app-readiness.v1', status: 'BLOCKED_EXTERNAL', configured: false, app_slug: null, required_permissions: { contents: 'read', pull_requests: 'read', checks: 'read', metadata: 'read' }, repository_selection: 'selected_repositories_recommended', installation_tokens: 'server-only', installations: [], repository_count: 0 };
  const page = await pageWithGitHubData(`${BASE}/prs`, undefined, undefined, blocked);
  await page.waitForFunction(() => document.body.innerText.includes('GitHub App activation required'));
  const text = await page.evaluate(() => document.body.innerText);
  assert.match(text, /blocked until an operator configures/);
  assert.doesNotMatch(text, /INSTALL GITHUB APP/);
  await page.close();
});

test('detail external link opens the validated GitHub URL in a new tab', async () => {
  const page = await pageWithGitHubData(`${BASE}/pr-detail?number=42`);
  await page.waitForFunction(() => document.body.innerText.includes('OPEN ON GITHUB'));
  const popupPromise = new Promise(resolve => page.once('popup', resolve));
  await clickRendered(page, '::-p-text(OPEN ON GITHUB ↗)');
  const popup = await popupPromise;
  assert.equal(popup.url(), 'https://github.com/acme/ship/pull/42');
  await popup.close();
  await page.close();
});

test('invalid external URL is rejected without opening an empty tab', async () => {
  const page = await pageWithGitHubData(`${BASE}/pr-detail?number=42`, 'javascript:document.write("bad")');
  await page.waitForFunction(() => document.body.innerText.includes('OPEN ON GITHUB'));
  await page.evaluate(() => {
    window.__openCalls = 0;
    window.open = () => { window.__openCalls += 1; return null; };
    window.alert = () => {};
  });
  await clickRendered(page, '::-p-text(OPEN ON GITHUB ↗)');
  assert.equal(await page.evaluate(() => window.__openCalls), 0);
  await page.close();
});
