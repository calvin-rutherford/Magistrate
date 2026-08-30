import os
import httpx
from typing import Dict, Any, List
from app.providers.base import ProviderAdapter

class DiscordProviderAdapter(ProviderAdapter):
    def provider_name(self) -> str:
        return 'discord'

    def is_configured(self) -> bool:
        return False

    def default_scopes(self) -> List[str]:
        return ['identify', 'messages.read']

    def get_authorization_url(self, state: str = '') -> str:
        client_id = self.client_id or os.getenv('DISCORD_OAUTH_CLIENT_ID', '')
        if not client_id:
            raise RuntimeError('Discord OAuth is not configured.')
        return f'https://discord.com/api/oauth2/authorize?client_id={client_id}&response_type=code&state={state}'

    async def exchange_code(self, code: str) -> Dict[str, Any]:
        raise RuntimeError('Discord OAuth exchange is not configured for this deployment.')

    async def refresh_token(self, refresh_token: str) -> Dict[str, Any]:
        return {'access_token': refresh_token}

    async def get_user_profile(self, access_token: str) -> Dict[str, Any]:
        raise RuntimeError('Discord profile lookup is not configured for this deployment.')

    def capabilities(self) -> List[str]:
        return ['read_messages', 'send_notifications']
