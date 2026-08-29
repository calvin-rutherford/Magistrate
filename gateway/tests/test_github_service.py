import pytest

from app.github_service import GitHubService, GitHubServiceError


LIST_OUTPUT = '''count: 1 of 1 total
pull_requests[1]{number,title,state,author,draft,review,body,created,merged_at,url}:
  42,"Fix navigation, safely",open,captain,no,review required,"## Summary\\nReal data",2026-08-26T10:00:00Z,,"https://github.com/acme/ship/pull/42"
help[1]:
  Run `gh-axi pr view <number>` to view details
'''

DETAIL_OUTPUT = '''pull_request:
  number: 42
  title: Fix navigation, safely
  state: open
  author: captain
  draft: no
  checks: "3 passed, 1 failed"
  body: "## Summary\\nReal data"
  reviews: []
'''


def test_list_parser_normalizes_authoritative_fields():
    service = GitHubService("acme/ship")
    item = service._parse_list(LIST_OUTPUT)[0]
    assert item["number"] == 42
    assert item["repository"] == "acme/ship"
    assert item["review_status"] == "REVIEW_REQUIRED"
    assert item["requires_attention"] is True
    assert item["url"] == "https://github.com/acme/ship/pull/42"


def test_list_parser_preserves_rows_with_multiline_bodies():
    output = LIST_OUTPUT.replace('## Summary\\nReal data', '## Summary\nReal data')
    item = GitHubService('acme/ship')._parse_list(output)[0]
    assert item['number'] == 42
    assert item['body'] == '## Summary\nReal data'


def test_list_parser_reanchors_columns_after_unescaped_body_quotes():
    output = LIST_OUTPUT.replace('## Summary\\nReal data', 'Summary, with "quoted" detail')
    item = GitHubService('acme/ship')._parse_list(output)[0]
    assert item['created_at'] == '2026-08-26T10:00:00Z'
    assert item['url'].endswith('/pull/42')


@pytest.mark.asyncio
async def test_pagination_and_cache_do_not_repeat_cli_call(monkeypatch):
    service = GitHubService("acme/ship", cache_ttl=60)
    calls = []
    async def fake_run(*args):
        calls.append(args)
        return LIST_OUTPUT
    monkeypatch.setattr(service, "_run", fake_run)
    first = await service.get_pull_requests(per_page=1)
    second = await service.get_pull_requests(per_page=1)
    assert first["items"][0]["title"] == "Fix navigation, safely"
    assert second["cached"] is True
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_merged_pull_requests_use_real_merge_data(monkeypatch):
    service = GitHubService("acme/ship")
    calls = []
    async def fake_run(*args):
        calls.append(args)
        return LIST_OUTPUT.replace(',open,', ',merged,').replace(',2026-08-26T10:00:00Z,,', ',2026-08-26T10:00:00Z,2026-08-27T12:00:00Z,')
    monkeypatch.setattr(service, '_run', fake_run)
    items = await service.get_merged_pull_requests()
    assert items[0]['merged_at'] == '2026-08-27T12:00:00Z'
    assert calls[0][2:4] == ('--state', 'merged')


@pytest.mark.asyncio
async def test_detail_normalizes_checks_without_credentials(monkeypatch):
    service = GitHubService("acme/ship")
    monkeypatch.setattr(service, "_run", lambda *args: _async_value(DETAIL_OUTPUT if "view" in args else LIST_OUTPUT))
    item = await service.get_pull_request(42)
    assert item["checks"]["status"] == "FAILING"
    assert item["checks"]["passed"] == 3
    assert "token" not in item


async def _async_value(value):
    return value


@pytest.mark.asyncio
async def test_rate_limit_error_is_safe(monkeypatch):
    service = GitHubService("acme/ship")
    async def fail(*args):
        raise GitHubServiceError("GitHub rate limit reached; try again later")
    monkeypatch.setattr(service, "_run", fail)
    with pytest.raises(GitHubServiceError, match="rate limit"):
        await service.get_pull_requests()
