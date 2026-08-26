import os

# Tests use an explicit fixture credential. Production has no fallback token;
# deployments must inject MAGISTRATE_TOKEN themselves.
os.environ.setdefault('MAGISTRATE_TOKEN', 'magistrate-device-token-12345')
