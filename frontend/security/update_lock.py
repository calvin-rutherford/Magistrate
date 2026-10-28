#!/usr/bin/env python3
"""Resolve local consumer overrides without npm's initial relative-edge path bug.

npm 10's nodeFromEdge resolves an initial file override relative to its consumer;
fromPath (validation/npm ci) correctly resolves overrides from the project root.
Use absolute paths ONLY inside a disposable resolver fixture, then serialize the
same npm-generated resolutions project-relative. Never delete/recreate the lock
from scratch, change package versions/names, or run project install scripts here.
"""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--seed', type=Path, default=ROOT / 'package-lock.json', help='Existing reviewed lock to preserve unrelated placements')
args = parser.parse_args()
package = json.loads((ROOT / 'package.json').read_text())
artifacts = json.loads((ROOT / 'security/artifacts.json').read_text())
local = {'file:security/artifacts/' + a['artifact'] for a in artifacts}
seen = set()
def absolute_overrides(overrides):
    for key, value in overrides.items():
        if isinstance(value, dict):
            absolute_overrides(value)
        elif value.startswith('file:'):
            assert value in local
            seen.add(value)
            overrides[key] = 'file:' + str(ROOT / value[5:])
absolute_overrides(package['overrides'])
assert seen == local
# Keep all mutable resolver state inside this worktree.
cache = ROOT.parent / '.release/foundation-p2'
cache.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix='lock-resolver-', dir=cache) as temporary:
    temporary = Path(temporary)
    (temporary / 'package.json').write_text(json.dumps(package))
    lock = json.loads(args.seed.read_text())
    # Same upstream versions are intentional. Invalidate only the source-derived
    # consumer resolutions so npm cannot reuse old same-version registry nodes.
    # All other locked packages remain inputs; npm owns dependency pruning.
    lock['packages'] = {location: entry for location, entry in lock['packages'].items()
                        if not any(location.endswith('node_modules/' + a['name'])
                                   and entry.get('version') == a['version'] for a in artifacts)}
    (temporary / 'package-lock.json').write_text(json.dumps(lock))
    subprocess.run(['npm', 'install', '--package-lock-only', '--ignore-scripts',
                    '--cache', str(cache / 'npm-cache')], cwd=temporary, check=True)
    # Re-evaluate the existing parser bridge after changing Metro override scope;
    # npm install alone may retain an invalid nested same-lock resolution.
    subprocess.run(['npm', 'update', 'image-size', '--package-lock-only', '--ignore-scripts',
                    '--cache', str(cache / 'npm-cache')], cwd=temporary, check=True)
    lock = json.loads((temporary / 'package-lock.json').read_text())
    assert all(entry['version'] == '2.0.4' for location, entry in lock['packages'].items()
               if location.endswith('node_modules/image-size')), 'Parser bridge resolution changed'
    (cache / 'resolver-lock.json').write_text(json.dumps(lock, indent=2) + '\n')
    for location, entry in lock['packages'].items():
        for artifact in artifacts:
            if location.endswith('node_modules/' + artifact['name']) and entry['version'] == artifact['version']:
                assert entry['integrity'] == artifact['integrity'], f'Unreplaced consumer: {location}'
                entry['resolved'] = 'file:security/artifacts/' + artifact['artifact']
    (ROOT / 'package-lock.json').write_text(json.dumps(lock, indent=2) + '\n')
