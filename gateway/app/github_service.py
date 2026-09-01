import asyncio
import csv
import io
import json
import os
import re
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional


class GitHubServiceError(RuntimeError):
    """A safe, user-facing failure from the server-side GitHub session."""


@dataclass
class _CacheEntry:
    expires_at: float
    value: Any


class GitHubService:
    """Read PRs through the captain's authenticated gh-axi session."""

    def __init__(self, repo: Optional[str] = None, cache_ttl: int = 45):
        self.repo = repo or os.getenv("MAGISTRATE_GITHUB_REPO", "calvin-rutherford/Magistrate")
        self.cache_ttl = cache_ttl
        self._cache: Dict[str, _CacheEntry] = {}
        self._lock = asyncio.Lock()

    def _command(self) -> str:
        command = os.getenv("GH_AXI_BIN") or shutil.which("gh-axi")
        if not command:
            raise GitHubServiceError("GitHub CLI session is unavailable on the gateway")
        return command

    async def _run(self, *args: str) -> str:
        env = os.environ.copy()
        env["GH_REPO"] = self.repo
        proc = await asyncio.create_subprocess_exec(
            self._command(), *args, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env=env,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode:
            message = stderr.decode("utf-8", "replace").strip()
            if "rate limit" in message.lower():
                raise GitHubServiceError("GitHub rate limit reached; try again later")
            raise GitHubServiceError(message or "GitHub request failed")
        return stdout.decode("utf-8", "replace")

    @staticmethod
    def _bool(value: str) -> bool:
        return value.strip().lower() in {"yes", "true", "1"}

    @staticmethod
    def _summary(body: str, title: str) -> str:
        text = re.sub(r"[#*_>`\[\]-]", " ", body or "")
        text = " ".join(text.split())
        return (text[:237] + "...") if len(text) > 240 else (text or title)

    @staticmethod
    def _timestamp(value: str) -> Optional[str]:
        value = (value or '').strip()
        if not value or value.lower() in {'no', 'none', 'unknown'}:
            return None
        relative = re.fullmatch(r'(\d+)([mhd]) ago', value.lower())
        if relative:
            amount = int(relative.group(1))
            delta = {'m': timedelta(minutes=amount), 'h': timedelta(hours=amount), 'd': timedelta(days=amount)}[relative.group(2)]
            return (datetime.now(timezone.utc) - delta).isoformat().replace('+00:00', 'Z')
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')

    def _normalize_row(self, row: List[str]) -> Dict[str, Any]:
        # gh-axi's table renderer can leave quotes inside a quoted PR body
        # unescaped. Re-anchor the stable trailing timestamp/URL columns so body
        # punctuation cannot shift authoritative merge fields.
        if len(row) > 10:
            row = row[:6] + [','.join(row[6:-3])] + row[-3:]
        number, title, state, author, draft, review, body, created, merged_at, url = (row + [""] * 10)[:10]
        review_status = review.upper().replace(" ", "_") if review and review != "none" else "NONE"
        state = state.upper()
        is_draft = self._bool(draft)
        return {
            "id": int(number), "number": int(number), "title": title,
            "repository": self.repo, "author": author, "branch": None,
            "state": state, "is_draft": is_draft, "mergeable": "UNKNOWN",
            "review_status": review_status,
            "checks": {"status": "UNKNOWN", "passed": 0, "failed": 0, "pending": 0, "summary": "Open details for checks"},
            "reviews": [], "created_at": self._timestamp(created), "updated_at": None,
            "merged_at": self._timestamp(merged_at),
            "summary": self._summary(body, title),
            "body": body or "", "requires_attention": state == "OPEN" and not is_draft and review_status != "APPROVED",
            "url": url,
        }

    def _parse_list(self, output: str) -> List[Dict[str, Any]]:
        marker = re.search(r"pull_requests\[\d+\].*?:\n", output)
        if not marker:
            return []
        rows: List[Dict[str, Any]] = []
        table = output[marker.end():].split('\nhelp[', 1)[0]
        for parsed in csv.reader(io.StringIO(table), skipinitialspace=True):
            if parsed and parsed[0].strip().isdigit():
                parsed[0] = parsed[0].strip()
                rows.append(self._normalize_row(parsed))
        return rows

    @staticmethod
    def _parse_detail(output: str) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for line in output.splitlines():
            match = re.match(r"^  ([a-z_]+):\s*(.*)$", line)
            if not match:
                continue
            key, value = match.groups()
            if value.startswith('"') and value.endswith('"'):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    value = value[1:-1]
            result[key] = value
        return result

    @staticmethod
    def _check_summary(value: str) -> Dict[str, Any]:
        counts = re.search(r"(\d+) passed, (\d+) failed", value or "")
        passed, failed = (int(counts.group(1)), int(counts.group(2))) if counts else (0, 0)
        status = "FAILING" if failed else ("PASSING" if passed else "UNKNOWN")
        return {"status": status, "passed": passed, "failed": failed, "pending": 0, "summary": value or "Not available"}

    async def get_pull_requests(self, page: int = 1, per_page: int = 20, refresh: bool = False) -> Dict[str, Any]:
        page, per_page = max(1, page), min(50, max(1, per_page))
        key = f"list:{page}:{per_page}"
        async with self._lock:
            cached = self._cache.get(key)
            if cached and cached.expires_at > time.monotonic() and not refresh:
                return {**cached.value, "cached": True}
            output = await self._run("pr", "list", "--state", "open", "--limit", str(page * per_page + 1), "--fields", "body,createdAt,mergedAt,url")
            all_prs = self._parse_list(output)
            start = (page - 1) * per_page
            value = {"items": all_prs[start:start + per_page], "page": page, "per_page": per_page,
                     "has_more": len(all_prs) > start + per_page, "cached": False}
            self._cache[key] = _CacheEntry(time.monotonic() + self.cache_ttl, value)
            return value

    async def get_merged_pull_requests(self, limit: int = 20, refresh: bool = False) -> List[Dict[str, Any]]:
        limit = min(50, max(1, limit))
        key = f'merged:{limit}'
        async with self._lock:
            cached = self._cache.get(key)
            if cached and cached.expires_at > time.monotonic() and not refresh:
                return cached.value
            output = await self._run('pr', 'list', '--state', 'merged', '--limit', str(limit), '--fields', 'body,createdAt,mergedAt,url')
            value = [item for item in self._parse_list(output) if item.get('merged_at')]
            self._cache[key] = _CacheEntry(time.monotonic() + self.cache_ttl, value)
            return value

    async def get_pull_request(self, number: int, refresh: bool = False) -> Dict[str, Any]:
        key = f"detail:{number}"
        async with self._lock:
            cached = self._cache.get(key)
            if cached and cached.expires_at > time.monotonic() and not refresh:
                return {**cached.value, "cached": True}
            detail_output, list_output = await asyncio.gather(
                self._run("pr", "view", str(number), "--full", "--reviews"),
                self._run("pr", "list", "--state", "all", "--limit", "100", "--fields", "body,createdAt,mergedAt,url"),
            )
            detail = self._parse_detail(detail_output)
            if not detail.get("number"):
                raise GitHubServiceError("Pull request was not found")
            review_status = str(detail.get("review", "NONE")).upper().replace(" ", "_")
            state = str(detail.get("state", "OPEN")).upper()
            draft = self._bool(str(detail.get("draft", "no")))
            list_item = next((item for item in self._parse_list(list_output) if item["number"] == number), {})
            value = {
                "id": int(detail["number"]), "number": int(detail["number"]), "title": detail.get("title", "Pull request"),
                "repository": self.repo, "author": detail.get("author", "unknown"), "branch": detail.get("branch"),
                "state": state, "is_draft": draft, "mergeable": str(detail.get("mergeable", "UNKNOWN")).upper(),
                "review_status": review_status, "checks": self._check_summary(str(detail.get("checks", ""))), "reviews": [],
                "created_at": detail.get("created") or list_item.get("created_at"), "updated_at": detail.get("updated"),
                "merged_at": detail.get("merged") if str(detail.get("merged", "")).lower() not in {"", "no", "none", "unknown"} else list_item.get("merged_at"),
                "summary": self._summary(str(detail.get("body", "")), str(detail.get("title", ""))), "body": detail.get("body", ""),
                "requires_attention": state == "OPEN" and not draft and review_status != "APPROVED",
                "url": detail.get("url") or f"https://github.com/{self.repo}/pull/{number}", "cached": False,
            }
            self._cache[key] = _CacheEntry(time.monotonic() + self.cache_ttl, value)
            return value

github_service = GitHubService()
