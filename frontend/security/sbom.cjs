// Preserve npm identities/hashes/graph and add explicit local-modification provenance.
const assert = require('node:assert/strict');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const sources = require('./sources.json');
const artifacts = require('./artifacts.json');
const bom = JSON.parse(execFileSync('npm', ['sbom', '--sbom-format', 'cyclonedx'], {
  cwd: path.resolve(__dirname, '..'), encoding: 'utf8', maxBuffer: 10 * 1024 * 1024,
}));
for (const artifact of artifacts) {
  const source = sources.find(s => s.key === artifact.key);
  const component = bom.components.find(c => c.name === artifact.name && c.version === artifact.version);
  assert(component, `Missing SBOM component ${artifact.key}`);
  assert(component.hashes.some(h => h.alg === 'SHA-512' && h.content === Buffer.from(artifact.integrity.slice(7), 'base64').toString('hex')));
  const distribution = component.externalReferences.find(r => r.type === 'distribution');
  assert(distribution.url.endsWith('/' + artifact.artifact));
  distribution.url = 'file:security/artifacts/' + artifact.artifact;
  component.properties.push(...Object.entries({
    'magistrate:modified-upstream': 'true', 'magistrate:source-url': source.url,
    'magistrate:source-sha256': source.sha256, 'magistrate:source-git-head': source.git_head,
    'magistrate:artifact-sha256': artifact.sha256, 'magistrate:patch-sha256': source.patch_sha256,
    'magistrate:source-manifest': 'frontend/security/sources.json#' + source.key,
  }).map(([name, value]) => ({ name, value })));
}
assert(!bom.components.some(c => ['braces', 'micromatch', 'node-forge'].includes(c.name)));
console.log(JSON.stringify(bom, null, 2));
