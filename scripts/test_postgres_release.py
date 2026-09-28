#!/usr/bin/env python3
"""Run the CI PostgreSQL contracts against ONLY a newly created disposable DB.

No operator DSN, existing container, worker, or deployment is inspected. Docker
is a test dependency, not the production database/isolation implementation.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def test_environment(state: Path, port: int) -> dict[str, str]:
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("MAGISTRATE_", "OPENAI_", "ANTHROPIC_", "GOOGLE_", "GITHUB_", "STRIPE_"))
        and key != "FM_HOME"
    }
    environment.update(
        MAGISTRATE_ENV="test",
        MAGISTRATE_SECRET_KEY="PzjyXlt95cBlNsdqacmjgHZjMqTiRBJ2_wF5iWkYBss=",
        MAGISTRATE_DATABASE_URL=f"postgresql://postgres:magistrate_release_test@127.0.0.1:{port}/magistrate_release_test",
        MAGISTRATE_STATE_DIR=str(state),
    )
    return environment


def main() -> int:
    container = None
    children: list[subprocess.Popen] = []
    release = ROOT / ".release"
    release.mkdir(exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="postgres-", dir=release) as temporary:
            container = subprocess.check_output([
                "docker", "run", "--detach", "--rm", "--label", "magistrate.release-test=true",
                "--publish", "127.0.0.1::5432",
                "--env", "POSTGRES_PASSWORD=magistrate_release_test",
                "--env", "POSTGRES_DB=magistrate_release_test", "postgres:16-alpine",
            ], text=True, timeout=180).strip()
            if not re.fullmatch(r"[0-9a-f]{64}", container):
                container = None
                raise RuntimeError("Docker did not return an owned test container identity")
            for _ in range(60):
                ready = subprocess.run(
                    ["docker", "exec", container, "pg_isready", "-U", "postgres"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
                )
                if ready.returncode == 0:
                    break
                time.sleep(1)
            else:
                raise RuntimeError("Disposable PostgreSQL did not become ready")
            address = subprocess.check_output(
                ["docker", "port", container, "5432/tcp"], text=True, timeout=10,
            ).strip()
            if not re.fullmatch(r"127\.0\.0\.1:[0-9]+", address):
                raise RuntimeError("Test database must bind only loopback")
            environment = test_environment(Path(temporary), int(address.rsplit(":", 1)[1]))
            # Keep these identical to the Gateway CI contract: concurrent startup,
            # persisted domain rows, tenant denials and selective account erasure.
            for tenant in ("tenant-a", "tenant-b"):
                children.append(subprocess.Popen(
                    ["uv", "run", "python", "-m", "scripts.postgres_smoke", tenant],
                    cwd=ROOT / "gateway", env=environment,
                ))
            codes = [child.wait(timeout=180) for child in children]
            if any(codes):
                raise RuntimeError("Concurrent PostgreSQL smoke failed")
            subprocess.run(
                ["uv", "run", "python", "-m", "scripts.postgres_verify_isolation"],
                cwd=ROOT / "gateway", env=environment, check=True, timeout=180,
            )
        print("COMPLETE: disposable PostgreSQL persistence (not production restore)")
        return 0
    except (OSError, RuntimeError, subprocess.SubprocessError):
        print("FAILED: disposable PostgreSQL contract; Docker, uv and locked Gateway dependencies required")
        return 1
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=10)
        if container is not None:
            subprocess.run(["docker", "rm", "--force", container], check=True, timeout=30,
                           stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    raise SystemExit(main())
