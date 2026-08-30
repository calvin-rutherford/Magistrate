import os
import httpx
from typing import Dict, Any, List
from app.providers.base import ProviderAdapter

class GitHubProviderAdapter(ProviderAdapter):
    def provider_name(self) -> str:
        return 'github'

    def is_configured(self) -> bool:
        return bool(self.client_id or os.getenv('GITHUB_OAUTH_CLIENT_ID')) and bool(self.client_secret or os.getenv('GITHUB_OAUTH_CLIENT_SECRET')) and bool(self.redirect_uri or os.getenv('MAGISTRATE_OAUTH_CALLBACK_BASE_URL'))

    def default_scopes(self) -> List[str]:
        return ['repo', 'read:org', 'user:email']

    def get_authorization_url(self, state: str = '') -> str:
        client_id = self.client_id or os.getenv('GITHUB_OAUTH_CLIENT_ID', '')
        callback_base = self.redirect_uri or os.getenv('MAGISTRATE_OAUTH_CALLBACK_BASE_URL', '')
        if not client_id or not callback_base.startswith(('https://', 'http://localhost', 'http://127.0.0.1', 'http://[::1]')):
            raise RuntimeError('GitHub OAuth is not configured.')
        redirect = callback_base.rstrip('/') + '/api/v1/auth/github/callback'
        scopes_str = '%20'.join(self.default_scopes())
        from urllib.parse import quote
        return f'https://github.com/login/oauth/authorize?client_id={quote(client_id)}&scope={scopes_str}&redirect_uri={quote(redirect)}&state={quote(state)}'

    async def exchange_code(self, code: str) -> Dict[str, Any]:
        client_id = self.client_id or os.getenv('GITHUB_OAUTH_CLIENT_ID', '')
        client_secret = self.client_secret or os.getenv('GITHUB_OAUTH_CLIENT_SECRET', '')
        if not client_id or not client_secret:
            raise RuntimeError('GitHub OAuth is not configured.')

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
        if not access_token:
            raise RuntimeError('GitHub access token is missing.')
        async with httpx.AsyncClient() as client:
            res = await client.get('https://api.github.com/user', headers={'Authorization': f'token {access_token}'})
            return res.json()

    def capabilities(self) -> List[str]:
        return ['read_prs', 'manage_repos', 'execute_firstmate']
