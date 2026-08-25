import os
import httpx
from typing import Dict, Any, List
from app.providers.base import ProviderAdapter

class GitHubProviderAdapter(ProviderAdapter):
    def provider_name(self) -> str:
        return 'github'

    def default_scopes(self) -> List[str]:
        return ['repo', 'read:org', 'user:email']

    def get_authorization_url(self, state: str = '') -> str:
        client_id = self.client_id or os.getenv('GITHUB_OAUTH_CLIENT_ID', 'demo_github_client_id')
        scopes_str = '%20'.join(self.default_scopes())
        return f'https://github.com/login/oauth/authorize?client_id={client_id}&scope={scopes_str}&state={state}'

    async def exchange_code(self, code: str) -> Dict[str, Any]:
        client_id = self.client_id or os.getenv('GITHUB_OAUTH_CLIENT_ID', '')
        client_secret = self.client_secret or os.getenv('GITHUB_OAUTH_CLIENT_SECRET', '')
        if not client_id or not client_secret:
            return {'access_token': 'demo_github_token_calvin_rutherford', 'username': 'calvin-rutherford', 'status': 'connected'}

        async with httpx.AsyncClient() as client:
            res = await client.post(
                'https://github.com/login/oauth/access_token',
                data={'client_id': client_id, 'client_secret': client_secret, 'code': code},
                headers={'Accept': 'application/json'}
            )
            data = res.json()
            return data

    async def refresh_token(self, refresh_token: str) -> Dict[str, Any]:
        return {'access_token': refresh_token}

    async def get_user_profile(self, access_token: str) -> Dict[str, Any]:
        if not access_token or access_token.startswith('demo_'):
            return {'username': 'calvin-rutherford', 'id': '1049281', 'name': 'Calvin Rutherford'}
        async with httpx.AsyncClient() as client:
            res = await client.get('https://api.github.com/user', headers={'Authorization': f'token {access_token}'})
            return res.json()

    def capabilities(self) -> List[str]:
        return ['read_prs', 'manage_repos', 'execute_firstmate']
