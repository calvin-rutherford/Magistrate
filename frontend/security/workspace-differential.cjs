// Compare original ordered matching and bundled resolver against the replacements.
// Originals live only in the hash-verified temporary control fixture, never in the
// installed frontend. Non-adversarial compatibility corpus; depth attack is separate.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const originals = path.resolve(process.argv[2]);
const micromatch = require(path.join(originals, 'micromatch'));
const matches = require('../node_modules/find-yarn-workspace-root/workspace-match');
const originalResolver = require(path.join(originals, 'resolve-workspace-root'));
const resolver = require('../node_modules/resolve-workspace-root');
const paths = ['app', 'packages/app', 'packages/a', 'packages/b', 'packages/ab', 'apps/app',
  'packages/nested/app', 'packages/.hidden', '.hidden', 'a.js', 'a.d.ts', 'package.json', 'other', 'x/y'];
const patterns = ['*', '**', 'packages/*', 'apps/*', 'packages/**', '**/app', 'packages/{a,b}',
  '{packages,apps}/*', 'packages/@(app|other)', 'packages/!(app)', 'packages/!(other)',
  'packages/[ab]*', 'packages/.*', '**/*.js', '**/*.d.ts', '!packages/*', '!**/app', '!other',
  '!!packages/*', '!(other)', 'packages/{1..3}', 'packages/a?', 'packages/app'];
const lists = [[], ...patterns.map(p => [p])];
for (const first of patterns) for (const second of patterns) lists.push([first, second]);
for (const first of patterns) lists.push([first, '!packages/*', 'packages/app']);
let comparisons = 0;
for (const candidate of paths) for (const list of lists) {
  assert.equal(matches(candidate, list), micromatch([candidate], list).length > 0,
    JSON.stringify({ candidate, list }));
  comparisons++;
}
(async () => {
  let resolverComparisons = 0;
  const temporary = fs.mkdtempSync(path.join(path.dirname(originals), 'workspace-'));
  try {
    const child = path.join(temporary, 'packages/app'); fs.mkdirSync(child, { recursive: true });
    for (const list of lists) {
      fs.writeFileSync(path.join(temporary, 'package.json'), JSON.stringify({ workspaces: list }));
      for (const start of [temporary, child]) {
        assert.equal(resolver.resolveWorkspaceRoot(start), originalResolver.resolveWorkspaceRoot(start));
        assert.equal(await resolver.resolveWorkspaceRootAsync(start), await originalResolver.resolveWorkspaceRootAsync(start));
        resolverComparisons += 2;
      }
    }
    for (const yaml of ['packages: ["packages/*", "!other"]', 'packages: ["other"]', 'invalid: [yaml']) {
      fs.writeFileSync(path.join(temporary, 'pnpm-workspace.yaml'), yaml);
      for (const options of [{}, { pnpmWorkspaces: false }, { packageWorkspaces: false }]) {
        assert.equal(resolver.resolveWorkspaceRoot(child, options), originalResolver.resolveWorkspaceRoot(child, options));
        assert.deepEqual(resolver.getWorkspaceGlobs(temporary, options), originalResolver.getWorkspaceGlobs(temporary, options));
        assert.deepEqual(await resolver.getWorkspaceGlobsAsync(temporary, options), await originalResolver.getWorkspaceGlobsAsync(temporary, options));
        resolverComparisons += 3;
      }
    }
  } finally { fs.rmSync(temporary, { recursive: true, force: true }); }
  console.log(JSON.stringify({ orderedMatcherComparisons: comparisons, resolverComparisons, mismatches: 0 }));
})().catch(error => { console.error(error); process.exitCode = 1; });
