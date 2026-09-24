# Magi execution live acceptance

Status: **operator runbook; opt-in real-provider and real-worker check**.

This runbook verifies the complete Magi-to-Firstmate handoff with the exact request:

> Create a small test project called magi-execution-test with a README containing "Hello from Magistrate."

It complements `gateway/tests/test_magi_execution_handoff.py`. That focused test
uses a representative external worker boundary; this run must use a real provider,
the production objective dispatcher, and a real Firstmate worker. A passing unit
test is not a substitute for this live check.

The committed, sanitized result shape is
[`evidence/magi-execution-live-acceptance-v1.json`](evidence/magi-execution-live-acceptance-v1.json).
Keep run-specific IDs, timestamps, commits, screenshots, and logs in private
operator evidence rather than updating that file with environment-specific data.

## Acceptance gates

A run passes only when all of these statements are independently observed:

1. The real Magi provider receives the exact request and selects
   `firstmate.submit_objective` exactly once.
2. Exactly one objective-tool invocation, one dispatcher invocation, one Magi
   objective row, and one Firstmate objective exist.
3. A real Firstmate worker starts. The acceptance driver does not create the
   requested directory or README itself.
4. The worker creates `magi-execution-test/README.md` as a regular, non-symlink
   file with exact bytes `Hello from Magistrate.\n`, and commits only that path.
5. Before typed completion evidence is accepted, Chat contains the user request
   and an acknowledgement but no completion claim.
6. Only after independent artifact verification and durable
   `objective.completed` evidence may Magi create the completion message.
7. Canonical and rendered Chat contain no terminal, Herdr, raw tool, objective,
   task, or acceptance-harness internals.
8. Replaying the same Chat client ID and the same completion event creates no
   additional objective, provider call, dispatch, or Chat message.
9. Any temporary browser HTTPS route is loopback-only, retains normal Gateway
   authentication, exposes no unrelated service, and is fully retired after
   capture.

A model description of work, a task row without a worker, worker status prose,
a harness exit, or a README created by the acceptance driver is not sufficient.
Do not infer one gate from another.

## Safety and isolation

1. Obtain operator approval for real-provider use, the isolated worker, and any
   temporary HTTPS route.
2. Start from the intended clean merged revision and record its commit privately.
3. Use a disposable Gateway database, a disposable Firstmate home, and a
   generated non-default Herdr lab session. Use only
   `$FM_HOME/bin/fm-herdr-lab.sh` for the lab session lifecycle; never stop or
   delete the default Herdr session.
4. Keep the Gateway and temporary HTTPS listener on loopback. Use a dedicated
   test principal and a task-local browser profile.
5. Load provider credentials through the normal secret source. Never print,
   copy into a command transcript, or place them in the evidence directory.
6. Before submission, prove the isolated database has zero conversations,
   messages, objective submissions, execution objectives, and execution events.
7. Record only bounded metadata at the provider boundary: call index, start and
   finish time, offered tool names, selected tool names, finish reason, and
   request/response hashes. An observer must delegate unchanged to the real
   provider and must not record prompts, provider text, headers, credentials, or
   tool arguments. Keep that observer outside the repository and delete it with
   the lab.
8. Do not change Activity, Attention, onboarding, UI, deployment, or unrelated
   resources as part of this run.

## Procedure

### 1. Submit once and prove routing

Submit the exact request once through the authenticated Native Chat endpoint
with a fresh client message ID. Preserve the request bytes in private evidence.

At the provider boundary require:

- tools offered: `firstmate.submit_objective`, `magi.respond`;
- required, non-parallel tool choice;
- exactly one selected function call;
- selected function: `firstmate.submit_objective`.

Then independently require:

- `magi_chat_diagnostics.magi_tool_calls = 1`;
- one accepted `magi_objective_submissions` row with `attempt_count = 1`;
- one real dispatcher start and one accepted receipt with
  `already_present = false`;
- one matching task in the isolated Firstmate backlog.

Do not submit another request to repair or confirm the result.

### 2. Start real work

Process the queued objective through the ordinary Firstmate supervisor intake
and launch it through the pinned `fm-spawn.sh` path. The worker brief may state
the exact requested artifact, local-only delivery, and no push/deployment. It
must not contain commands that pre-create the artifact.

Observe backend kickoff separately from the objective rows:

- the spawn receipt identifies a real worker and disposable worktree;
- a read-only, lab-scoped Herdr observation sees that worker as active in the
  expected worktree;
- the worker, not the acceptance driver, writes and commits the artifact.

### 3. Verify the artifact independently

After the worker reports completion, verify without editing its worktree:

```sh
python3 - <<'PY'
import hashlib
import os
from pathlib import Path

root = Path(os.environ["WORKER_WORKTREE"])
project = root / "magi-execution-test"
readme = project / "README.md"
expected = b"Hello from Magistrate.\n"

assert project.is_dir() and not project.is_symlink()
assert readme.is_file() and not readme.is_symlink()
actual = readme.read_bytes()
assert actual == expected
assert hashlib.sha256(actual).hexdigest() == (
    "fa412454cc59e13c016f2569ceab7cff88d2af3f928ef9d6494fd63a4c96cc6f"
)
PY

git -C "$WORKER_WORKTREE" status --short
git -C "$WORKER_WORKTREE" diff-tree --no-commit-id --name-status -r HEAD
```

Require a clean worker worktree and exactly:

```text
A magi-execution-test/README.md
```

Record the worker commit in private evidence, but do not put a run-specific
commit into the sanitized committed evidence.

### 4. Prove completion is evidence-gated

Read canonical Chat after the artifact exists but before publishing completion
evidence. It must contain exactly two messages: the exact user request and one
assistant acknowledgement. The acknowledgement must not claim that the project
is complete or verified.

Publish `firstmate.execution-event.v1` facts through the authenticated producer
route. At minimum, preserve distinct accepted observations for
`objective.accepted`, `worker.started`, and `objective.completed`. The terminal
event must carry `firstmate.completion-evidence.v1` with:

- `result = completed`;
- `verification = verified`;
- at least one passed `acceptance` check for the independently verified project
  and README;
- only allowlisted, bounded artifact references.

Set the completion occurrence time from the independent verification time, not
from worker prose. Require the event transaction to persist before the
completion provider call starts. The final completion row must be a new
assistant-only message replying to the original user message.

The expected ordering is:

```text
provider selects objective tool
< objective dispatch accepted
< real worker starts
< worker writes artifact
< pre-evidence Chat read has no completion claim
< independent artifact verification passes
< objective.completed evidence persists
< completion provider call starts
< completion Chat row finalizes
```

### 5. Check canonical and rendered Chat isolation

Canonical Chat must end with exactly three messages: one user request, one
acceptance acknowledgement, and one verified completion update. Search the
joined message content case-insensitively for all of these sentinels, plus the
actual opaque objective and task IDs:

```text
firstmate
submit_objective
tool call
tool result
objective_id
task_id
herdr
terminal
harness
```

Require zero hits. Also require these persisted diagnostics to remain zero:

```text
legacy_chat_reads
terminal_chat_reads
pi_ownership_chat_reads
```

For rendered proof, export the current frontend against a captain-approved
temporary HTTPS origin. Serve only the static export and required authenticated
Gateway routes. Bind the listener to loopback, keep normal bearer authentication
in force, and use a short-lived task-local certificate. Require:

- an unauthenticated Magi read returns 401;
- an authenticated Magi read returns the canonical three-message transcript;
- an unknown route returns 404;
- `chrome-devtools-axi` renders `/chat` from the temporary HTTPS origin without
  submitting a message;
- the live rendered DOM contains all three canonical messages;
- the rendered DOM has zero sentinel/opaque-ID hits;
- the captured network page contains only the expected temporary origin and
  successful required requests.

A screenshot is supporting evidence, not the transcript authority. Inspect it
for accidental private data before retaining it.

### 6. Prove idempotency

Replay the original Chat request with the identical client message ID. Require
`duplicate = true`, the same canonical message identities, no provider-call
increase, and objective/dispatcher counts still equal to one.

Replay the byte-identical completion event with its identical event ID. Require
`status = duplicate`, the same completion message identity, no provider-call
increase, and final message count still equal to three.

### 7. Retire all temporary access

1. Stop the named browser session.
2. Stop the temporary HTTPS process and prove its listener is absent.
3. Delete its private key, certificate, and task-local browser authentication.
4. Stop the isolated Gateway and prove its listener is absent.
5. Trigger Herdr teardown through the original guarded lab owner. Prove the lab
   session is absent and the default-session snapshot is byte-identical to its
   pre-run tripwire.
6. Scan retained evidence for the bootstrap secret, bearer, encryption key,
   authorization headers, provider credentials, and private key. A hit is a
   failed evidence package: destroy and recollect it.
7. Remove all raw authentication material. Do not commit the disposable
   database, server scripts, browser profile, certificates, provider observer,
   raw logs, or screenshots.

## Evidence to retain

The private operator report should include exact commands, bounded outputs,
UTC ordering, provider-call count and selected tools, objective/dispatch counts,
worker-start proof, artifact bytes/hash and commit, pre/post-evidence Chat,
leak-check result, replay deltas, listener/certificate cleanup, and Herdr
tripwire result.

The repository may retain only the sanitized aggregate shape in
`docs/evidence/magi-execution-live-acceptance-v1.json`. It deliberately omits
credentials, bearer/session material, prompts beyond the captain-owned request,
raw provider content, raw auth, database contents, task/conversation/objective
IDs, environment paths, ports, hostnames, timestamps, commit IDs, session names,
and screenshots.

## Verdict

Record **PASS** only if every gate above passes with one request and one
objective. Record **NO-GO** immediately for a second objective/dispatch, mock or
manual artifact creation, a completion claim before accepted evidence, Chat
internal leakage, idempotency drift, authentication bypass, retained temporary
listener/key, or a changed default Herdr session.
