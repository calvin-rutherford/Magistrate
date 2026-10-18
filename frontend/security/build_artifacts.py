#!/usr/bin/env python3
"""Reproduce private consumer artifacts, never publish or alter upstream identity.

python3 frontend/security/build_artifacts.py --output .release/reproduced
Use --check to compare with committed artifacts.json; all inputs are hash pinned.
Requires Python >=3.12 and POSIX patch. No npm/Node install or build secrets.
"""
import argparse
import base64
import gzip
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parent

def digest(data):
    return hashlib.sha256(data).hexdigest()


def build(output):
    output.mkdir(parents=True, exist_ok=True)
    results = []
    for source in json.loads((ROOT / 'sources.json').read_text()):
        with urllib.request.urlopen(source['url'], timeout=60) as response:
            data = response.read(30 * 1024 * 1024)
        assert digest(data) == source['sha256'], 'Upstream SHA-256 mismatch'
        assert 'sha512-' + base64.b64encode(hashlib.sha512(data).digest()).decode() == source['integrity'], 'Upstream SHA-512 mismatch'
        patch = (ROOT / source['patch']).read_bytes()
        assert digest(patch) == source['patch_sha256'], 'Patch digest mismatch'
        with tempfile.TemporaryDirectory(dir=output) as temporary:
            temporary = Path(temporary)
            with tarfile.open(fileobj=io.BytesIO(data)) as archive:
                # Explicitly reject nonregular data/symlinks even though data filter
                # already rejects traversal. No upstream install scripts are run.
                assert all(member.isfile() or member.isdir() for member in archive.getmembers())
                archive.extractall(temporary, filter='data')
            package = temporary / 'package'
            subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-i', str(ROOT / source['patch'])], cwd=package, check=True)
            for relative in source['remove']:
                (package / relative).unlink()
            for relative, overlay in source['overlays'].items():
                content = (ROOT / overlay['path']).read_bytes()
                assert digest(content) == overlay['sha256'], 'Overlay digest mismatch'
                (package / relative).parent.mkdir(parents=True, exist_ok=True)
                (package / relative).write_bytes(content)
            meta = json.loads((package / 'package.json').read_text())
            assert (meta['name'], meta['version']) == (source['name'], source['version'])
            assert not {'node-forge', 'braces', 'micromatch'} & meta.get('dependencies', {}).keys()
            assert any((package / name).is_file() for name in ('LICENSE', 'LICENSE.md', 'LICENSE.txt')), 'Upstream license missing'
            # Source attribution travels inside the installed package and SBOM.
            (package / 'SOURCE.json').write_text(json.dumps(source, indent=2, sort_keys=True) + '\n')
            inventory = {}
            tar_bytes = io.BytesIO()
            with tarfile.open(fileobj=tar_bytes, mode='w', format=tarfile.USTAR_FORMAT) as archive:
                for file in sorted(package.rglob('*')):
                    if not file.is_file():
                        continue
                    rel = file.relative_to(package).as_posix()
                    content = file.read_bytes()
                    inventory[rel] = digest(content)
                    info = tarfile.TarInfo('package/' + rel)
                    info.size = len(content)
                    info.mode = 0o755 if file.stat().st_mode & 0o111 else 0o644
                    # uid/gid/mtime default zero; no wall time, directory order or
                    # local path enters the artifact. gzip filename is empty.
                    archive.addfile(info, io.BytesIO(content))
            packed = gzip.compress(tar_bytes.getvalue(), mtime=0)
            sha = digest(packed)
            name = sha + '.tgz'
            (output / name).write_bytes(packed)
            results.append({'key': source['key'], 'name': source['name'], 'version': source['version'],
                            'artifact': name, 'sha256': sha,
                            'integrity': 'sha512-' + base64.b64encode(hashlib.sha512(packed).digest()).decode(),
                            'files': inventory})
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    results = build(args.output.resolve())
    if args.check:
        assert results == json.loads((ROOT / 'artifacts.json').read_text()), 'Artifact reproducibility mismatch'
    else:
        (args.output / 'artifacts.json').write_text(json.dumps(results, indent=2, sort_keys=True) + '\n')
