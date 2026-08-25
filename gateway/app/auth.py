import os
from fastapi import HTTPException, Header, Query, status

MAGISTRATE_TOKEN = os.getenv('MAGISTRATE_TOKEN', 'magistrate-device-token-12345')

def verify_token(x_magistrate_token: str = Header(None, alias='X-Magistrate-Token'), token: str = Query(None)) -> str:
    token_val = x_magistrate_token or token
    if not token_val or token_val != MAGISTRATE_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Invalid or missing Magistrate device token'
        )
    return token_val
