import os
from typing import Dict, Any, List
from app.providers.base import ProviderAdapter

class TeamsProviderAdapter(ProviderAdapter):
    def provider_name(self) -> str:
        return 'teams'

    def default_scopes(self) -> List[str]:
        return ['User.Read', 'Chat.Read', 'ChannelMessage.Read.All']

    def get_authorization_url(self, state: str = '') -> str:
        client_id = self.client_id or os.getenv('TEAMS_OAUTH_CLIENT_ID', 'demo_teams_client_id')
        redirect = self.redirect_uri or 'http://100.84.181.23:8000/api/v1/auth/teams/callback'
        scopes_str = '%20'.join(self.default_scopes())
        return f'https://login.microsoftonline.com/common/oauth2/v2.0/authorize?client_id={client_id}&response_type=code&redirect_uri={redirect}&response_mode=query&scope={scopes_str}&state={state}'

    async def exchange_code(self, code: str) -> Dict[str, Any]:
        return {'access_token': 'demo_teams_access_token', 'username': 'calvin.rutherford@eversana.com', 'status': 'connected'}

    async def refresh_token(self, refresh_token: str) -> Dict[str, Any]:
        return {'access_token': refresh_token}

    async def get_user_profile(self, access_token: str) -> Dict[str, Any]:
        return {'username': 'calvin.rutherford@eversana.com', 'name': 'Calvin Rutherford', 'id': 'ms_graph_calvin_77'}

    def capabilities(self) -> List[str]:
        return ['read_mentions', 'read_channel_activity', 'read_chats']

    async def get_mentions(self) -> List[Dict[str, Any]]:
        # Teams mention retrieval is not implemented yet. An unavailable provider
        # must contribute no records rather than presenting a sample mention as live.
        return []
