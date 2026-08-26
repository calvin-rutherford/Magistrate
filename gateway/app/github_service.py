import asyncio
import json
import os
from typing import Dict, Any, List

class GitHubService:
    def __init__(self, repo: str = 'melkezic/firstmate'):
        self.repo = repo

    async def get_pull_requests(self) -> List[Dict[str, Any]]:
        try:
            proc = await asyncio.create_subprocess_exec(
                'gh', 'pr', 'list', '--repo', self.repo, '--json',
                'number,title,author,headRefName,state,reviewDecision,url,isDraft,mergeable,commits,statusCheckRollup',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode == 0 and stdout:
                raw_prs = json.loads(stdout.decode('utf-8'))
                formatted_prs = []
                for pr in raw_prs:
                    author_name = pr.get('author', {}).get('login', 'Firstmate')
                    branch = pr.get('headRefName', 'main')
                    
                    # Only show PRs made by firstmate/subagents
                    is_agent = 'agent' in author_name.lower() or 'firstmate' in author_name.lower() or 'bot' in author_name.lower() or branch.startswith('agent/') or branch.startswith('firstmate/')
                    if not is_agent:
                        continue
                        
                    state_str = 'draft' if pr.get('isDraft') else pr.get('state', 'OPEN').lower()
                    review_str = pr.get('reviewDecision') or 'REVIEW_REQUIRED'
                    checks = pr.get('statusCheckRollup') or []
                    checks_complete = bool(checks) and all(
                        (check.get('conclusion') or check.get('state')) in ('SUCCESS', 'NEUTRAL', 'SKIPPED')
                        for check in checks
                    )
                    merge_ready = (
                        state_str == 'open'
                        and not pr.get('isDraft')
                        and pr.get('mergeable') == 'MERGEABLE'
                        and review_str == 'APPROVED'
                        and checks_complete
                    )
                    formatted_prs.append({
                        'id': pr.get('number'),
                        'pr_number': pr.get('number'),
                        'title': pr.get('title', 'Pull Request'),
                        'repository': self.repo,
                        'author': author_name,
                        'agent': 'Firstmate',
                        'branch': pr.get('headRefName', 'main'),
                        'state': state_str,
                        'review_status': review_str,
                        'checks': 'passing' if checks_complete else 'pending',
                        'mergeable': pr.get('mergeable', 'MERGEABLE'),
                        'summary': pr.get('title', 'GitHub Pull Request'),
                        'requires_attention': merge_ready,
                        'merge_decision_required': merge_ready,
                        'head_sha': (pr.get('commits') or [{}])[-1].get('oid'),
                        'url': pr.get('url', f'https://github.com/{self.repo}/pulls')
                    })
                return formatted_prs
        except Exception as e:
            print('GitHubService error:', e)

        # Fallback to authentic repo telemetry structure if gh CLI has no open PRs
        return []

github_service = GitHubService()
