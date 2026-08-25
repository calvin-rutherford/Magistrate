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
                    'url': '/chat',
                    'requires_action': True
                })
        except Exception as e:
            print('Error fetching Firstmate attention:', e)

        # 2. GITHUB PULL REQUESTS
        try:
            prs = await github_service.get_pull_requests()
            for pr in prs:
                if pr.get('requires_attention') or pr.get('review_status') == 'REVIEW_REQUIRED':
                    items.append({
                        'id': f'github-pr-{pr.get("pr_number")}',
                        'provider': 'github',
                        'title': f'PR #{pr.get("pr_number")} Review Required',
                        'subtitle': f'{pr.get("title")} ({pr.get("repository")})',
                        'priority': 'MEDIUM',
                        'status': 'review_required',
                        'url': pr.get('url', 'https://github.com/melkezic/firstmate/pulls'),
                        'requires_action': True
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
                        'url': issue.get('url', 'https://eversana.atlassian.net'),
                        'requires_action': True
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
                        'url': m.get('url', 'https://teams.microsoft.com'),
                        'requires_action': True
                    })
        except Exception as e:
            print('Error fetching Teams attention:', e)

        return items

attention_service = AttentionService()
