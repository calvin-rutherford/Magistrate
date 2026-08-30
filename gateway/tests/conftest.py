import os
from pathlib import Path

from cryptography.fernet import Fernet


# Gateway imports initialize the SQLite schema. Keep the test database inside
# this disposable worktree and make the permitted non-production mode explicit.
os.environ.setdefault('MAGISTRATE_ENV', 'test')
os.environ.setdefault('MAGISTRATE_SECRET_KEY', Fernet.generate_key().decode('ascii'))
TEST_DB_PATH = Path(__file__).parent / '.gateway-test.sqlite3'
# A disposable test database must not leak state between local invocations.
TEST_DB_PATH.unlink(missing_ok=True)
os.environ.setdefault('MAGISTRATE_DB_PATH', str(TEST_DB_PATH))
