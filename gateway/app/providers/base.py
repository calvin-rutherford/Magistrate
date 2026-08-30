from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
import os

class ProviderAdapter(ABC):
    def __init__(self, client_id: str = '', client_secret: str = '', redirect_uri: str = ''):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri

    @abstractmethod
    def provider_name(self) -> str:
        pass

    def is_configured(self) -> bool:
        """Whether an operator supplied real OAuth client configuration."""
        return bool(self.client_id and self.client_secret)

    def unavailable_reason(self) -> str:
        return 'OAuth is not configured for this provider.'

    @abstractmethod
    def default_scopes(self) -> List[str]:
        pass

    @abstractmethod
    def get_authorization_url(self, state: str = '') -> str:
        pass

    @abstractmethod
    async def exchange_code(self, code: str) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def refresh_token(self, refresh_token: str) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def get_user_profile(self, access_token: str) -> Dict[str, Any]:
        pass

    @abstractmethod
    def capabilities(self) -> List[str]:
        pass
