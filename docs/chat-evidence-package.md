# Safe chat evidence package

MVP scorecard item 4 (2026-08-31) is covered by the existing terminal filter firewall plus the fixtures below.

## Real Herdr snapshot check

`docs/evidence/real-herdr-snapshot-check.txt` is the output of a live run against the current Herdr pane using `HerdrClient.read_agent_output(..., output_format='ansi')`, followed by `parse_agent_history`. The captured output passed with zero harness/terminal rows in the visible conversation. Tool rows remained typed as tools and therefore stay hidden under the default chat preference. The check used the real Herdr CLI/socket; it did not use a fixture or lifecycle command.

To repeat (replace the pane id with one from `herdr api snapshot`):

```sh
PYTHONPATH=gateway python3 - <<'PY'
import asyncio
from app.herdr_client import HerdrClient, parse_agent_history
async def check():
    raw = await HerdrClient().read_agent_output('w1W:p2', lines=200, output_format='ansi')
    rows = parse_agent_history(raw)
    assert not any(row['kind'] == 'conversation' and any(x in row['text'] for x in ('FIRSTMATE_OP', 'pane_id=', 'jsonrpc', 'worktree=/', '$ ')) for row in rows)
    print('PASS: visible conversation contains no harness/terminal rows')
asyncio.run(check())
PY
```

## Fixtures and truthful states

- `frontend/tests/chat-evidence.test.ts` covers unknown worker/user records failing closed, local timestamps (including accessible full timestamps), and the exact streaming, gateway-failure, and cancellation labels.
- `frontend/tests/chat-terminal.web.test.js` covers the browser cancellation interruption path, snapshot growth/reconciliation, metadata firewall, worker-thread firewall, and persisted timestamp behavior.
- The chat UI deliberately says `Updating response…`, `Response stopped before completion…`, and `Response stopped`; it does not fabricate a completed response after an interruption or gateway failure.

Run the focused checks with `npm run test:chat-evidence` and `npm run test:chat` from `frontend/`.

## Final wireframes

The committed brand-aligned wireframe sheet is [`wireframes/mvp-chat-surfaces.svg`](wireframes/mvp-chat-surfaces.svg). It includes chat, drawer, Attention approve/reject actions, fleet, voice, Connections, Settings, and gateway/cancel/offline failure states. Cyan, violet, amber, critical red, paper, and obsidian follow the Magistrate brand package.
