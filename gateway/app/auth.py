import hashlib
import hmac
import os
from dataclasses import dataclass
from typing import Optional

from fastapi import Header, HTTPException, status

# Authentication is intentionally fail-closed. A deployment must inject this
# secret; neither the gateway nor the Expo bundle provides a reusable default.
MAGISTRATE_TOKEN = os.getenv('MAGISTRATE_TOKEN', '').strip()


@dataclass(frozen=True)
class AuthenticatedActor:
    """The authenticated identity available to the current app architecture.

    The current gateway has one configured bearer credential rather than a
    user-account issuer. We therefore derive a non-secret actor handle from
    that credential and bind Voice sessions and audit records to it.
    """

    actor_id: str


def verify_token(
    authorization: Optional[str] = Header(None),
    x_magistrate_token: Optional[str] = Header(None, alias='X-Magistrate-Token'),
) -> AuthenticatedActor:
    if not MAGISTRATE_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Gateway authentication is not configured.'
        )

    bearer = ''
    if authorization and authorization.lower().startswith('bearer '):
        bearer = authorization[7:].strip()
    token_val = bearer or (x_magistrate_token or '').strip()
    if not token_val or not hmac.compare_digest(token_val, MAGISTRATE_TOKEN):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Invalid or missing Magistrate authentication.'
        )

    actor_id = f'actor_{hashlib.sha256(token_val.encode()).hexdigest()[:20]}'
    return AuthenticatedActor(actor_id=actor_id)
