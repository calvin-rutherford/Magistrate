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
os.environ.setdefault('MAGISTRATE_BOOTSTRAP_SECRET', 'test-bootstrap-secret')

from app.auth import issue_session

# All integration tests use a real bearer session rather than the retired
# shared device credential.  Individual tests issue narrower sessions when
# exercising scope boundaries.
TEST_SESSION_TOKEN = issue_session('test-bootstrap-secret')['session_token']
TEST_HEADERS = {'Authorization': f'Bearer {TEST_SESSION_TOKEN}'}
