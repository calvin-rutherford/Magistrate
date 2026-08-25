import os
import httpx
from typing import Dict, Any, List
from app.providers.base import ProviderAdapter

class GoogleProviderAdapter(ProviderAdapter):
    def provider_name(self) -> str:
        return 'google'

    def default_scopes(self) -> List[str]:
        return ['email', 'profile']

    def get_authorization_url(self, state: str = '') -> str:
        client_id = self.client_id or os.getenv('GOOGLE_OAUTH_CLIENT_ID', 'demo_google_client_id')
        return f'https://accounts.google.com/o/oauth2/v2/auth?client_id={client_id}&response_type=code&state={state}'

    async def exchange_code(self, code: str) -> Dict[str, Any]:
        return {'access_token': 'demo_google_token', 'username': 'spectre@gmail.com', 'status': 'connected'}

    async def refresh_token(self, refresh_token: str) -> Dict[str, Any]:
        return {'access_token': refresh_token}

    async def get_user_profile(self, access_token: str) -> Dict[str, Any]:
        return {'username': 'spectre@gmail.com', 'id': '1008291'}

    def capabilities(self) -> List[str]:
        return ['read_profile', 'cloud_access']
