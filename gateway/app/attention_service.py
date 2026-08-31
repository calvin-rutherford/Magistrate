import asyncio
from typing import Dict, Any, List
from app.firstmate_client import FirstmateClient
from app.herdr_client import HerdrClient
from app.github_service import github_service
from app.providers.jira import JiraProviderAdapter
from app.providers.teams import TeamsProviderAdapter

fm_client = FirstmateClient()
herdr_client = HerdrClient()
jira_adapter = JiraProviderAdapter()
teams_adapter = TeamsProviderAdapter()

class AttentionService:
    async def get_unified_attention_items(self) -> List[Dict[str, Any]]:
        items = []

        # 1. FIRSTMATE & HERDR BLOCKERS
        try:
            agents = await herdr_client.list_agents()
            fm_att = await fm_client.get_attention_items(herdr_agents=agents)
            for att in fm_att:
                items.append({
                    'id': att.get('id', 'fm-item'),
                    'provider': 'firstmate',
                    'title': att.get('title', 'Firstmate Action Required'),
                    'subtitle': att.get('subtitle', 'Agent blocked or decision needed'),
                    'priority': 'HIGH',
                    'status': att.get('status', 'blocked'),
                    'url': att.get('url', '/attention'),
                    'deep_link': att.get('deep_link'),
                    'target_id': att.get('target_id'),
                    'context': att.get('context'),
                    'requires_action': True,
                    'notification_kind': att.get('type'),
                    'consequential': att.get('consequential') is True,
                    'revision': att.get('revision')
                })
        except Exception as e:
            print('Error fetching Firstmate attention:', e)

        # 2. GITHUB PULL REQUESTS
        try:
            page = await github_service.get_pull_requests()
            for pr in page['items']:
                if pr.get('requires_attention') or pr.get('review_status') == 'REVIEW_REQUIRED':
                    item_id = f'github-pr-{pr.get("number")}'
                    items.append({
                        'id': item_id,
                        'provider': 'github',
                        'title': f'PR #{pr.get("number")} Review Required',
                        'subtitle': f'{pr.get("title")} ({pr.get("repository")})',
                        'priority': 'MEDIUM',
                        'status': 'review_required',
                        'url': f'/pr-detail?number={pr.get("number")}',
                        'requires_action': True,
                        'external_url': pr.get('url'),
                        'context': {'repository': pr.get('repository'), 'author': pr.get('author'), 'branch': pr.get('branch'), 'review_status': pr.get('review_status'), 'checks': (pr.get('checks') or {}).get('summary')},
                        'notification_kind': 'pr_ready',
                        'consequential': pr.get('merge_decision_required') is True,
                        'revision': pr.get('head_sha') or pr.get('updated_at'),
                        'deep_link': f'/pr-detail?number={pr.get("number")}' if pr.get('number') is not None else None
                    })
        except Exception as e:
            print('Error fetching GitHub attention:', e)

        # 3. JIRA ISSUES
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

        # 4. TEAMS MENTIONS
        try:
            mentions = await teams_adapter.get_mentions()
            for m in mentions:
                if m.get('requires_attention'):
                    items.append({
                        'id': m.get('id', 'teams-msg'),
                        'provider': 'teams',
                        'title': f'Teams Mention from {m.get("sender")}',
                        'subtitle': m.get('summary'),
                        'priority': 'HIGH',
                        'status': 'unread_mention',
                        'url': f'/attention?item={m.get("id", "teams-msg")}',
                        'external_url': m.get('url'),
                        'context': {'sender': m.get('sender'), 'message_id': m.get('id')},
                        'requires_action': True,
                        'notification_kind': 'captain_question',
                        'revision': m.get('updated_at') or m.get('id')
                    })
        except Exception as e:
            print('Error fetching Teams attention:', e)

        return items

attention_service = AttentionService()
