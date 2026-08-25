import os
import httpx
from typing import Dict, Any, List
from app.providers.base import ProviderAdapter

class TwitterProviderAdapter(ProviderAdapter):
    def provider_name(self) -> str:
        return 'twitter'

    def default_scopes(self) -> List[str]:
        return ['tweet.read', 'tweet.write', 'users.read']

    def get_authorization_url(self, state: str = '') -> str:
        client_id = self.client_id or os.getenv('TWITTER_OAUTH_CLIENT_ID', 'demo_twitter_client_id')
        return f'https://twitter.com/i/oauth2/authorize?response_type=code&client_id={client_id}&state={state}'

    async def exchange_code(self, code: str) -> Dict[str, Any]:
        return {'access_token': 'demo_twitter_token', 'username': '@spectre_dev', 'status': 'connected'}

    async def refresh_token(self, refresh_token: str) -> Dict[str, Any]:
        return {'access_token': refresh_token}

    async def get_user_profile(self, access_token: str) -> Dict[str, Any]:
        return {'username': '@spectre_dev', 'id': '998231'}

    def capabilities(self) -> List[str]:
        return ['read_feed', 'post_content']
