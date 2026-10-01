import { cpSync, readFileSync, rmSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const agentModules = join(root, 'node_modules', '@earendil-works', 'pi-coding-agent', 'node_modules');

for (const [name, expectedVersion] of [['brace-expansion', '5.0.12'], ['undici', '8.10.2']]) {
  const source = join(root, 'node_modules', name);
  const target = join(agentModules, name);
  const version = JSON.parse(readFileSync(join(source, 'package.json'), 'utf8')).version;
  if (version !== expectedVersion) throw new Error(`Expected patched ${name} ${expectedVersion}, found ${version}`);
  rmSync(target, { force: true, recursive: true });
  cpSync(source, target, { recursive: true });
}
