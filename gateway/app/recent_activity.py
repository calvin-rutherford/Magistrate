import asyncio
import os
from typing import Any, Dict, List

from app.github_service import GitHubService
from app.structured_runtime import StructuredRuntimeProjection


class RecentActivityService:
    """Merge persisted structured execution facts with forge merge events."""

    def __init__(
        self,
        runtime: StructuredRuntimeProjection,
        github: GitHubService,
        *,
        shared_provider_owner_only: bool = False,
    ):
        self.runtime = runtime
        self.github = github
        self.shared_provider_owner_only = shared_provider_owner_only

    async def get_recent_activity(
        self,
        owner_user_id: str,
        limit: int = 20,
        refresh: bool = False,
    ) -> Dict[str, Any]:
        github_read = (
            self.github.get_merged_pull_requests(limit=limit, refresh=refresh)
            if not self.shared_provider_owner_only
            or owner_user_id == os.getenv('MAGISTRATE_BOOTSTRAP_USER_ID', 'default_user').strip()
            else asyncio.sleep(0, result=PermissionError('shared provider data is owner-only'))
        )
        fleet_result, github_result = await asyncio.gather(
            asyncio.to_thread(self.runtime.recent_activity, owner_user_id, limit=limit),
            github_read,
            return_exceptions=True,
        )
        source_status = {
            'firstmate': 'unavailable' if isinstance(fleet_result, Exception) else 'available',
            'github': 'unavailable' if isinstance(github_result, Exception) else 'available',
        }
        if all(status == 'unavailable' for status in source_status.values()):
            raise RuntimeError('Recent activity sources are unavailable')

        items: List[Dict[str, Any]] = [] if isinstance(fleet_result, Exception) else list(fleet_result)
        if not isinstance(github_result, Exception):
            for pull in github_result:
                items.append({
                    'id': f'github:pull:{pull["number"]}:merged',
                    'type': 'pull_request_merged',
                    'title': pull['title'],
                    'description': f'PR #{pull["number"]} merged',
                    'occurred_at': pull['merged_at'],
                    'source': 'github',
                    'project': pull['repository'],
                    'url': pull['url'],
                    'pull_request_number': pull['number'],
                })

        # Prefer GitHub's precise merge event if a future structured completion
        # carries the same public URL.
        github_urls = {item['url'] for item in items if item['source'] == 'github' and item.get('url')}
        items = [
            item for item in items
            if item['source'] == 'github' or not item.get('url') or item['url'] not in github_urls
        ]
        items.sort(key=lambda item: item['occurred_at'], reverse=True)
        return {
            'items': items[:limit],
            'sources': source_status,
            'firstmate_source': 'persisted-structured-state',
        }
