import os
from typing import Dict, Any, List
from app.providers.base import ProviderAdapter

class JiraProviderAdapter(ProviderAdapter):
    def provider_name(self) -> str:
        return 'jira'

    def is_configured(self) -> bool:
        # No Jira OAuth application, redirect URI, or tenant consent exists for
        # owner alpha. Reporting False keeps the surface honestly unavailable.
        return False

    def is_deferred(self) -> bool:
        return True

    def unavailable_reason(self) -> str:
        return 'Jira is deferred for this release. It becomes available only when Jira OAuth credentials are configured.'

    def default_scopes(self) -> List[str]:
        return ['read:jira-user', 'read:jira-work', 'offline_access']

    def get_authorization_url(self, state: str = '') -> str:
        client_id = self.client_id or os.getenv('JIRA_OAUTH_CLIENT_ID', '')
        redirect = self.redirect_uri or os.getenv('MAGISTRATE_OAUTH_CALLBACK_BASE_URL', '') + '/api/v1/auth/jira/callback'
        if not client_id or not redirect.startswith(('https://', 'http://localhost', 'http://127.0.0.1', 'http://[::1]')):
            raise RuntimeError('Jira OAuth is not configured.')
        scopes_str = '%20'.join(self.default_scopes())
        return f'https://auth.atlassian.com/authorize?audience=api.atlassian.com&client_id={client_id}&scope={scopes_str}&redirect_uri={redirect}&state={state}&response_type=code&prompt=consent'

    async def exchange_code(self, code: str) -> Dict[str, Any]:
        raise RuntimeError('Jira OAuth exchange is not configured for this deployment.')

    async def refresh_token(self, refresh_token: str) -> Dict[str, Any]:
        return {'access_token': refresh_token}

    async def get_user_profile(self, access_token: str) -> Dict[str, Any]:
        raise RuntimeError('Jira profile lookup is not configured for this deployment.')

    def capabilities(self) -> List[str]:
        return ['read_assigned_issues', 'read_attention_issues', 'read_recent_activity']

    async def get_assigned_issues(self) -> List[Dict[str, Any]]:
        # Jira issue retrieval is not implemented yet. An unavailable provider must
        # contribute no records rather than presenting sample issues as live data.
        return []
