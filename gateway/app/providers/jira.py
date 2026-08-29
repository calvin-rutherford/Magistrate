import os
from typing import Dict, Any, List
from app.providers.base import ProviderAdapter

class JiraProviderAdapter(ProviderAdapter):
    def provider_name(self) -> str:
        return 'jira'

    def default_scopes(self) -> List[str]:
        return ['read:jira-user', 'read:jira-work', 'offline_access']

    def get_authorization_url(self, state: str = '') -> str:
        client_id = self.client_id or os.getenv('JIRA_OAUTH_CLIENT_ID', 'demo_jira_client_id')
        redirect = self.redirect_uri or 'http://100.84.181.23:8000/api/v1/auth/jira/callback'
        scopes_str = '%20'.join(self.default_scopes())
        return f'https://auth.atlassian.com/authorize?audience=api.atlassian.com&client_id={client_id}&scope={scopes_str}&redirect_uri={redirect}&state={state}&response_type=code&prompt=consent'

    async def exchange_code(self, code: str) -> Dict[str, Any]:
        return {'access_token': 'demo_jira_access_token', 'username': 'calvin@eversana.com', 'status': 'connected'}

    async def refresh_token(self, refresh_token: str) -> Dict[str, Any]:
        return {'access_token': refresh_token}

    async def get_user_profile(self, access_token: str) -> Dict[str, Any]:
        return {'username': 'calvin@eversana.com', 'name': 'Calvin Rutherford', 'account_id': 'jira_calvin_99'}

    def capabilities(self) -> List[str]:
        return ['read_assigned_issues', 'read_attention_issues', 'read_recent_activity']

    async def get_assigned_issues(self) -> List[Dict[str, Any]]:
        # Jira issue retrieval is not implemented yet. An unavailable provider must
        # contribute no records rather than presenting sample issues as live data.
        return []
