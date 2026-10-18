#!/usr/bin/env python3
"""Exercise pinned original vulnerabilities ONLY in a disposable ignored fixture.

No control package is an application dependency or ships in an artifact. Downloads
are verified before extraction; no package lifecycle scripts, credentials, external
provider calls or original malicious inputs outside bounded subprocesses are used.
"""
import base64
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parent
output = ROOT.parents[1] / '.release' / 'control-fixtures'
output.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(dir=output) as temporary:
    modules = Path(temporary) / 'node_modules'
    for source in json.loads((ROOT / 'control-sources.json').read_text()):
        with urllib.request.urlopen(source['url'], timeout=60) as response:
            data = response.read(10 * 1024 * 1024)
        assert hashlib.sha256(data).hexdigest() == source['sha256']
        assert 'sha512-' + base64.b64encode(hashlib.sha512(data).digest()).decode() == source['integrity']
        target = modules / source['name']
        target.mkdir(parents=True)
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            for member in archive.getmembers():
                assert member.isfile() or member.isdir()
                assert member.name.startswith('package/')
                member.name = member.name[len('package/'):]
                if member.name:
                    archive.extract(member, target, filter='data')
    subprocess.run(['node', str(ROOT / 'negative-controls.cjs'), str(modules)], check=True, timeout=60)
    subprocess.run(['node', str(ROOT / 'workspace-differential.cjs'), str(modules)], check=True, timeout=60)
