import os
from pathlib import Path

from cryptography.fernet import Fernet


# Gateway imports initialize the SQLite schema. Keep the test database inside
# this disposable worktree and make the permitted non-production mode explicit.
os.environ.setdefault('MAGISTRATE_ENV', 'test')
os.environ.setdefault('MAGISTRATE_SECRET_KEY', Fernet.generate_key().decode('ascii'))
os.environ.setdefault(
    'MAGISTRATE_DB_PATH', str(Path(__file__).parent / '.gateway-test.sqlite3')
)
