import os
import httpx
from typing import Dict, Any, List
from app.providers.base import ProviderAdapter

class TwitterProviderAdapter(ProviderAdapter):
    def provider_name(self) -> str:
        return 'twitter'

    def is_configured(self) -> bool:
        return False

    def default_scopes(self) -> List[str]:
        return ['tweet.read', 'tweet.write', 'users.read']

    def get_authorization_url(self, state: str = '') -> str:
        client_id = self.client_id or os.getenv('TWITTER_OAUTH_CLIENT_ID', '')
        if not client_id:
            raise RuntimeError('Twitter OAuth is not configured.')
        return f'https://twitter.com/i/oauth2/authorize?response_type=code&client_id={client_id}&state={state}'

    async def exchange_code(self, code: str) -> Dict[str, Any]:
        raise RuntimeError('Twitter OAuth exchange is not configured for this deployment.')

    async def refresh_token(self, refresh_token: str) -> Dict[str, Any]:
        return {'access_token': refresh_token}

    async def get_user_profile(self, access_token: str) -> Dict[str, Any]:
        raise RuntimeError('Twitter profile lookup is not configured for this deployment.')

    def capabilities(self) -> List[str]:
        return ['read_feed', 'post_content']
