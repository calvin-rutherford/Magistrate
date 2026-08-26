# Magistrate site-wide audit

> Status: evidence inventory and baseline checks recorded on 2026-08-26. Findings remain to be added in subsequent audit iterations. This document does not yet represent a completed audit.

## Audit method and claim standard

This audit evaluates the checked-out `fm/site-audit-gnhf-s9` baseline across UX, accessibility, reliability, performance, security/privacy, mobile/web consistency, test coverage, and operations. A **confirmed defect** must be demonstrated directly by repository code, a reproducible behavior, or check output. An **opportunity/risk** identifies a plausible concern that still needs validation. Findings will retain that distinction and avoid treating a PR description or an unrun test as proof.

Repository paths and symbols are the durable evidence links. Line references will be used only where they add clarity and are unlikely to become misleading during the active-PR merge sequence.

## Checked-out baseline

- Branch: `fm/site-audit-gnhf-s9`.
- Audit base and initial `HEAD`: `6f5c61c` (`Add quiet actionable attention notifications (#2)`), identical to `origin/main` when inspected.
- Initial worktree: clean (`git status --short` produced no output).
- Applicable project instructions: root `AGENTS.md` reserves project-intrinsic knowledge for that file and points Expo work to `frontend/AGENTS.md`; `frontend/AGENTS.md` requires the exact Expo 57 documentation before changing Expo code. This audit changes documentation only.
- Recent history inspected through `git log --oneline --decorate --max-count=12`, spanning the notification and terminal fixes plus the earlier UI, architecture, and voice work back through the initial MVP.

## Architecture and inspection inventory

The repository contains three runtime surfaces that require separate review:

1. `frontend/`: Expo Router / React Native 0.86 application targeting native and web. File-based screens currently include the tab routes `index`, `account`, `attention`, `chat`, `fleet`, `map`, and `prs`, plus top-level `gesture`, `status`, and `voice` screens. The authoritative route files are under `frontend/app/`; `frontend/app/_layout.tsx` installs the root error boundary and notification monitor, and `frontend/app/(tabs)/_layout.tsx` declares the hidden tab navigator.
2. `gateway/`: FastAPI service with account/auth, GitHub, Jira, Teams, unified attention, notifications, voice transcription, fleet/agent, captain terminal, and agent key-input endpoints in `gateway/app/main.py`; it also exposes the AR WebSocket router in `gateway/app/ar_glasses.py`. Its package and Python constraints are in `gateway/pyproject.toml` and lock state in `gateway/uv.lock`.
3. `backend/`: Django/Channels/Celery government and agent domain. HTTP routing is in `backend/og_broker/urls.py`, the Magistrate WebSocket route is in `backend/agents/routing.py`, and models/services/tasks live under `backend/agents/`.

Configuration and operational entry points inspected include `README.md`, `docs/architecture.md`, `docker-compose.yml`, root and component dependency manifests, `Dockerfile`, `start_magistrate.sh`, `magistrate.sh`, `setup.sh`, `setup_server.sh`, `push_to_vps.sh`, and `pull_migrations.sh`. Test inventory includes `frontend/tests/`, `gateway/tests/`, `backend/tests/`, and `tests/e2e_live_test.py`. Detailed source review is pending; baseline check results are recorded below.

## Active work and overlap boundaries

GitHub state was inspected with the required `~/.npm-global/bin/gh-axi` wrapper. At inspection time, the repository had three open PRs, all authored by `calvin-rutherford`, none with reviews, and none with configured CI checks (`0 passed, 0 failed`). PR descriptions report local validation, but this audit treats those reports as context rather than independently verified results.

| Workstream | Ref inspected | Diff against `origin/main` | Audit boundary / merge-order consequence |
| --- | --- | --- | --- |
| Ambient backgrounds and speech-reactive Voice Mode | PR #3, `origin/fm/ambient-backgrounds-b5` at `a8f2c66` | 15 files, +190/-157. Changes `EnvironmentBackground`, weather/theme services and assets, `WeatherBadge`, `voice.tsx`, recorder metering, and ambient tests. | Do not independently prescribe implementation of time/weather scenes, metered voice ripple, reduced-motion behavior, or high-contrast treatment already in this PR. Audit current-baseline defects separately, mark overlapping recommendations deferred to PR #3, and revalidate accessibility, privacy/configuration, performance, and native/web behavior after it lands. |
| Captain terminal history and scrolling | PR #4, `origin/fm/terminal-history-h8` at `2c864ce` | 6 files, +164/-29. Changes chat virtualization/follow behavior, terminal API/client history sizing, and web/gateway tests. | Do not duplicate scrollback expansion, virtualization, overlap-poll prevention, viewport preservation, or jump-to-latest work. Revalidate terminal UX, accessibility, API load, and regressions after PR #4; findings outside those boundaries may remain independently actionable. |
| GitHub/Home PR data and navigation | PR #5, `origin/fm/github-home-prs-g3` at `735aabd` | 15 files, +538/-127. Adds live/cached/paginated `gh-axi` PR data, Home/attention integration, in-app PR detail, external-link validation, and tests. | Defer replacement of mock/stale PR data, Home PR detail/navigation, pagination/cache/error handling, and external-link hardening to PR #5. Revalidate auth boundaries, parser resilience, UI states, notification compatibility, and performance after merge. |
| Quiet notifications | Baseline `6f5c61c`; feature ref `origin/fm/quiet-notifications-n6` at `31c242c` | The feature is already merged as PR #2 into `origin/main`; the historical feature diff touches 13 files across frontend and gateway notification/attention flows. | Audit notification behavior as baseline code, not future work. Do not characterize this as an active open PR. Findings must account for the merged transition/ack/preferences implementation and its existing tests. |
| Voice Mode prototype | `fm/voice-mode-prototype-a2` at `6f5c61c` | No commits ahead of the baseline; the ref resolves exactly to current `origin/main`. | There is no separate active Voice Mode diff to defer beyond PR #3. Audit the baseline `frontend/app/voice.tsx` and voice services normally, while treating PR #3's overlapping audio-reactivity changes as pending. |

The three open PRs overlap shared hotspots: PRs #3 and #4 both modify `frontend/package.json`; PRs #4 and #5 both modify `frontend/src/api/client.ts` and `gateway/app/main.py`; PR #5 also modifies notification-adjacent `frontend/app/(tabs)/attention.tsx`, `gateway/app/attention_service.py`, and `gateway/app/github_service.py` already changed by merged notification work. Recommendations touching these files must name dependencies and require post-merge revalidation rather than assume the branches combine cleanly.

## Evidence commands recorded

The following read-only commands established this inventory:

```text
git status --short
git branch --show-current
git branch -a --no-color
git log --oneline --decorate --max-count=12
git rev-parse --short <ref>
git log -1 --format='%h %s' <ref>
git diff --stat origin/main...<feature-ref>
git diff --name-status origin/main...<feature-ref>
rg --files
rg -n "@(app|router)\\.|app\\.(get|post|put|delete)|path\\(|urlpatterns|Stack\\.Screen|Tabs\\.Screen" backend gateway frontend/app -g '*.py' -g '*.tsx'
find frontend/app -maxdepth 3 -type f -name '*.tsx' -print | sort
find gateway/tests backend/tests frontend/tests tests -maxdepth 2 -type f | sort
~/.npm-global/bin/gh-axi pr list --state open --limit 20 --fields body,createdAt,url
~/.npm-global/bin/gh-axi pr view <3|4|5> --full
~/.npm-global/bin/gh-axi pr diff <3|4|5> --full
~/.npm-global/bin/gh-axi pr checks <3|4|5>
```

Refs compared: `origin/main`, `origin/fm/ambient-backgrounds-b5`, `origin/fm/terminal-history-h8`, `origin/fm/github-home-prs-g3`, `origin/fm/quiet-notifications-n6`, and `fm/voice-mode-prototype-a2`.

## Findings

Not yet populated. Subsequent iterations must add evidence-backed confirmed defects and separately labeled validation risks/opportunities, then link every high-priority item to acceptance criteria in `docs/site-wide-task-list.md`.

## Baseline verification

Checks were run independently on the checked-out baseline; active-PR validation claims were not counted. No long-running process remained after the commands completed.

| Surface | Exact command | Result | Interpretation / blocker |
| --- | --- | --- | --- |
| Frontend types | `cd frontend && npx tsc --noEmit` | Passed (exit 0). | The baseline type-checks with the installed dependencies. |
| Frontend terminal web test | `cd frontend && npm run test:chat` | Passed: 2 tests, 0 failures (exit 0). | Covers a bounded terminal viewport/wheel interaction and phone-sized composer input; it is not a site-wide browser suite. |
| Frontend lint | `cd frontend && npm run lint` | Failed: 7 errors and 11 warnings (exit 1). | No ESLint config was committed, so Expo first generated `frontend/eslint.config.js`; the generated file was removed immediately to preserve documentation-only scope. Reported errors include `react-hooks/set-state-in-effect` in the Account, Attention, Home, and PR routes and web color-scheme hook; `react-hooks/immutability` in `app/voice.tsx`; and `react-hooks/refs` in `EnvironmentBackground`. These are confirmed check failures, but each underlying user impact still requires source/behavior review before it becomes a prioritized defect. |
| Gateway tests, initial invocation | `cd gateway && uv run pytest -q` | Blocked during collection: 5 modules failed with `ModuleNotFoundError: No module named 'app'` (exit 2). | The project does not configure its local `app` import path for a plain pytest invocation. The corrected invocation below establishes the actual test baseline; this initial failure is still an operational/test-runner setup risk. |
| Gateway tests, corrected import path | `cd gateway && PYTHONPATH=. uv run pytest -q` | Passed: 23 tests, 1 warning (exit 0). | The warning is a Starlette deprecation from `fastapi.testclient`: use of `httpx` through that module is deprecated in favor of `httpx2`. |
| Backend tests | `cd backend && python3 -m pytest -q` | Blocked immediately: `/usr/bin/python3: No module named pytest` (exit 1). | No ready backend test environment was present. Installing dependencies would exceed this documentation-only iteration and could mutate environment/lock state; backend test status is therefore unknown, not passing. |

The frontend lint bootstrap created the only non-documentation artifact observed during checking; after removing it, `git status --short` showed only this audit document as changed. Full Expo web/native builds and the live end-to-end test were not run in this bounded baseline-check iteration and must not be inferred from the passing typecheck or terminal test.
