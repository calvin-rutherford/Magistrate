import os
from typing import Dict, Any, List
from app.providers.base import ProviderAdapter

class TeamsProviderAdapter(ProviderAdapter):
    def provider_name(self) -> str:
        return 'teams'

    def is_configured(self) -> bool:
        # No Microsoft Teams OAuth application, redirect URI, or tenant consent exists for
        # owner alpha. Reporting False keeps the surface honestly unavailable.
        return False

    def is_deferred(self) -> bool:
        return True

    def unavailable_reason(self) -> str:
        return 'Microsoft Teams is deferred for this release. It becomes available only when Microsoft Teams OAuth credentials are configured.'

    def default_scopes(self) -> List[str]:
        return ['User.Read', 'Chat.Read', 'ChannelMessage.Read.All']

    def get_authorization_url(self, state: str = '') -> str:
        client_id = self.client_id or os.getenv('TEAMS_OAUTH_CLIENT_ID', '')
        redirect = self.redirect_uri or os.getenv('MAGISTRATE_OAUTH_CALLBACK_BASE_URL', '') + '/api/v1/auth/teams/callback'
        if not client_id or not redirect.startswith(('https://', 'http://localhost', 'http://127.0.0.1', 'http://[::1]')):
            raise RuntimeError('Teams OAuth is not configured.')
        scopes_str = '%20'.join(self.default_scopes())
        return f'https://login.microsoftonline.com/common/oauth2/v2.0/authorize?client_id={client_id}&response_type=code&redirect_uri={redirect}&response_mode=query&scope={scopes_str}&state={state}'

    async def exchange_code(self, code: str) -> Dict[str, Any]:
        raise RuntimeError('Teams OAuth exchange is not configured for this deployment.')

    async def refresh_token(self, refresh_token: str) -> Dict[str, Any]:
        return {'access_token': refresh_token}

    async def get_user_profile(self, access_token: str) -> Dict[str, Any]:
        raise RuntimeError('Teams profile lookup is not configured for this deployment.')

    def capabilities(self) -> List[str]:
        return ['read_mentions', 'read_channel_activity', 'read_chats']

    async def get_mentions(self) -> List[Dict[str, Any]]:
        # Teams mention retrieval is not implemented yet. An unavailable provider
        # must contribute no records rather than presenting a sample mention as live.
        return []
