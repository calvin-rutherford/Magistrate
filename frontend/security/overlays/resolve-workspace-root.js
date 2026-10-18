'use strict';
// Unbundled translation of byCedric/resolve-workspace-root@2.0.1 (MIT).
// Exact source: 72f675ae9ea952e832c63f1138346406b0d8630b, src/index.ts and
// src/utils/{dirs,files,workspaces}.ts. See SOURCE.json for source hashes.
// Changes: ordered matcher via picomatch; js-yaml is a visible locked dependency,
// not an old hidden bundle. Public sync/async, precedence and error rules retained.
const path = require('node:path');
const fs = require('node:fs');
const { load: parseYaml } = require('js-yaml');
const workspaceMatch = require('./workspace-match');
function expectedMissing(error) { return 'code' in error && (error.code === 'ENOENT' || error.code === 'EISDIR'); }
function read(file) {
  try { return fs.readFileSync(file, 'utf8'); }
  catch (error) { if (expectedMissing(error)) return null; throw error; }
}
async function readAsync(file) {
  try { return await fs.promises.readFile(file, 'utf8'); }
  catch (error) { if (expectedMissing(error)) return null; throw error; }
}
function packageGlobs(content) {
  try {
    const data = JSON.parse(content);
    if (Array.isArray(data?.workspaces)) return data.workspaces;
    if (Array.isArray(data?.workspaces?.packages)) return data.workspaces.packages;
  } catch (error) { if (error.name !== 'SyntaxError') throw error; }
  return null;
}
function pnpmGlobs(content) {
  try {
    const data = content ? parseYaml(content) : null;
    if (Array.isArray(data?.packages)) return data.packages;
  } catch (error) { if (error.name !== 'YAMLException') throw error; }
  return null;
}
function* directories(start) {
  let current = path.normalize(start);
  let previous;
  do {
    yield [current, path.relative(current, start)];
    previous = current; current = path.dirname(current);
  } while (current !== previous);
}
function* configurations(options) {
  if (options.pnpmWorkspaces !== false) yield ['pnpm-workspace.yaml', pnpmGlobs];
  if (options.packageWorkspaces !== false) yield ['package.json', packageGlobs];
}
function resolveWorkspaceRoot(start = process.cwd(), options = {}) {
  for (const [current, relative] of directories(start)) {
    for (const [name, parse] of configurations(options)) {
      const content = read(path.join(current, name));
      const globs = content && parse(content);
      if (globs && (!relative || workspaceMatch(relative, globs))) return current;
    }
  }
  return null;
}
async function resolveWorkspaceRootAsync(start = process.cwd(), options = {}) {
  for (const [current, relative] of directories(start)) {
    for (const [name, parse] of configurations(options)) {
      const content = await readAsync(path.join(current, name));
      const globs = content && parse(content);
      if (globs && (!relative || workspaceMatch(relative, globs))) return current;
    }
  }
  return null;
}
function getWorkspaceGlobs(root = process.cwd(), options = {}) {
  for (const [name, parse] of configurations(options)) {
    const content = read(path.join(root, name));
    const globs = content && parse(content);
    if (globs) return globs;
  }
  return null;
}
async function getWorkspaceGlobsAsync(root = process.cwd(), options = {}) {
  for (const [name, parse] of configurations(options)) {
    const content = await readAsync(path.join(root, name));
    const globs = content && parse(content);
    if (globs) return globs;
  }
  return null;
}
module.exports = { resolveWorkspaceRoot, resolveWorkspaceRootAsync, getWorkspaceGlobs, getWorkspaceGlobsAsync };
