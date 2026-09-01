import os
import httpx
from typing import Dict, Any, List
from app.providers.base import ProviderAdapter

class GoogleProviderAdapter(ProviderAdapter):
    def provider_name(self) -> str:
        return 'google'

    def is_configured(self) -> bool:
        # Token exchange/profile lookup is intentionally not implemented yet;
        # do not advertise a partial OAuth integration as usable.
        return False

    def default_scopes(self) -> List[str]:
        return ['email', 'profile']

    def get_authorization_url(self, state: str = '') -> str:
        client_id = self.client_id or os.getenv('GOOGLE_OAUTH_CLIENT_ID', '')
        if not client_id:
            raise RuntimeError('Google OAuth is not configured.')
        return f'https://accounts.google.com/o/oauth2/v2/auth?client_id={client_id}&response_type=code&state={state}'

    async def exchange_code(self, code: str) -> Dict[str, Any]:
        raise RuntimeError('Google OAuth exchange is not configured for this deployment.')

    async def refresh_token(self, refresh_token: str) -> Dict[str, Any]:
        return {'access_token': refresh_token}

    async def get_user_profile(self, access_token: str) -> Dict[str, Any]:
        raise RuntimeError('Google profile lookup is not configured for this deployment.')

    def capabilities(self) -> List[str]:
        return ['read_profile', 'cloud_access']
