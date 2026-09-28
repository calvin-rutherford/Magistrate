import asyncio
import os
from typing import Dict, Any, List, Optional

from app.firstmate_decisions import FirstmateDecisionService, firstmate_decisions
from app.github_app import github_app_service
from app.providers.jira import JiraProviderAdapter
from app.providers.teams import TeamsProviderAdapter

jira_adapter = JiraProviderAdapter()
teams_adapter = TeamsProviderAdapter()


class AttentionService:
    """Project persisted decisions plus configured provider attention.

    Firstmate and Herdr are never polled here. This service is used by both GET
    routes and the notification timer, so keeping it process-free is the
    read-does-not-mutate runtime boundary.
    """

    def __init__(
        self,
        decision_service: FirstmateDecisionService = firstmate_decisions,
        *,
        shared_provider_owner_only: bool = False,
    ):
        self.decision_service = decision_service
        self.shared_provider_owner_only = shared_provider_owner_only

    async def get_unified_attention_items(self, owner_user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        owner_user_id = owner_user_id or os.getenv("MAGISTRATE_BOOTSTRAP_USER_ID", "default_user").strip()
        items = []

        # Structured captain holds enter through the decision event/store seam.
        # Reads expose that owner-qualified projection without refreshing a
        # shell snapshot or consulting pane state.
        try:
            decisions, source = await asyncio.gather(
                self.decision_service.reconcile(owner_user_id),
                asyncio.to_thread(
                    self.decision_service.store.source_status,
                    owner_user_id,
                    self.decision_service.source_instance_id,
                ),
            )
            items.extend(self.decision_service.attention_items(
                owner_user_id,
                decisions=decisions,
                stale=source["status"] not in {"available", "unobserved"},
            ))
        except Exception:
            # Persistence failures can carry local paths; expose no exception
            # detail and continue with independent providers.
            print('Persisted Firstmate decisions unavailable')

        # GitHub App reads are installation- and principal-qualified. They are
        # safe for every tenant and remain separate from execution-runtime state.
        try:
            page = await github_app_service.get_pull_requests(owner_user_id)
            for pr in page['items']:
                if pr.get('requires_attention') or pr.get('review_status') == 'REVIEW_REQUIRED':
                    repository_id = pr.get('repository_id')
                    item_id = f'github-repository-{repository_id}-pr-{pr.get("number")}'
                    detail_url = f'/pr-detail?number={pr.get("number")}' + (f'&repositoryId={repository_id}' if repository_id is not None else '')
                    items.append({
                        'id': item_id,
                        'provider': 'github',
                        'title': f'PR #{pr.get("number")} Review Required',
                        'subtitle': f'{pr.get("title")} ({pr.get("repository")})',
                        'priority': 'MEDIUM',
                        'status': 'review_required',
                        'url': detail_url,
                        'requires_action': True,
                        'external_url': pr.get('url'),
                        'context': {'repository': pr.get('repository'), 'author': pr.get('author'), 'branch': pr.get('branch'), 'review_status': pr.get('review_status'), 'checks': (pr.get('checks') or {}).get('summary')},
                        'notification_kind': 'pr_ready',
                        'consequential': pr.get('merge_decision_required') is True,
                        'revision': pr.get('head_sha') or pr.get('updated_at'),
                        'deep_link': detail_url if pr.get('number') is not None else None
                    })
        except Exception as e:
            print('Error fetching GitHub attention:', e)

        # Jira/Teams still use deployment-level provider authority. Never
        # project that operator data into another authenticated tenant.
        if (
            self.shared_provider_owner_only
            and owner_user_id != os.getenv("MAGISTRATE_BOOTSTRAP_USER_ID", "default_user").strip()
        ):
            return items

        try:
            issues = await jira_adapter.get_assigned_issues()
            for issue in issues:
                if issue.get('requires_attention'):
                    items.append({
                        'id': f'jira-{issue.get("key")}',
                        'provider': 'jira',
                        'title': f'Jira Issue {issue.get("key")}',
                        'subtitle': issue.get('title'),
                        'priority': issue.get('priority', 'HIGH'),
                        'status': issue.get('status', 'IN PROGRESS'),
                        'url': f'/attention?item=jira-{issue.get("key")}',
                        'external_url': issue.get('url'),
                        'context': {'issue_key': issue.get('key'), 'project': issue.get('project')},
                        'requires_action': True,
                        'notification_kind': 'blocker',
                        'revision': issue.get('updated_at') or issue.get('status')
                    })
        except Exception as e:
            print('Error fetching Jira attention:', e)

        try:
            mentions = await teams_adapter.get_mentions()
            for mention in mentions:
                if mention.get('requires_attention'):
                    items.append({
                        'id': mention.get('id', 'teams-msg'),
                        'provider': 'teams',
                        'title': f'Teams Mention from {mention.get("sender")}',
                        'subtitle': mention.get('summary'),
                        'priority': 'HIGH',
                        'status': 'unread_mention',
                        'url': f'/attention?item={mention.get("id", "teams-msg")}',
                        'external_url': mention.get('url'),
                        'context': {'sender': mention.get('sender'), 'message_id': mention.get('id')},
                        'requires_action': True,
                        'notification_kind': 'captain_question',
                        'revision': mention.get('updated_at') or mention.get('id')
                    })
        except Exception as e:
            print('Error fetching Teams attention:', e)

        return items


attention_service = AttentionService(shared_provider_owner_only=True)
