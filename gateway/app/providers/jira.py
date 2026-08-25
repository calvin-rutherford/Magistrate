import os
import httpx
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
        return [
            {
                'id': 'AI-214',
                'key': 'AI-214',
                'title': 'PMR migration schema review & validation',
                'project': 'AI Engineering',
                'status': 'IN PROGRESS',
                'priority': 'HIGH',
                'assignee': 'Calvin Rutherford',
                'summary': 'Production PMR backend migration changes database schema',
                'requires_attention': True,
                'url': 'https://eversana.atlassian.net/browse/AI-214'
            },
            {
                'id': 'AI-189',
                'key': 'AI-189',
                'title': 'Setup EVERSANA enterprise security boundary',
                'project': 'Security & Compliance',
                'status': 'IN REVIEW',
                'priority': 'MEDIUM',
                'assignee': 'Calvin Rutherford',
                'summary': 'Define sensitivity boundaries for internal LLM prompts',
                'requires_attention': False,
                'url': 'https://eversana.atlassian.net/browse/AI-189'
            }
        ]
