# Harness and model switching

Magistrate treats execution identity as observed runtime data, not a guess. Fleet
rows show the harness and model reported by Herdr or the matched Firstmate task;
missing fields are shown as **unknown**. A configured capability or a saved
preference is not evidence that a running process uses that identity.

## Defaults for new and restarted agents

**Settings → Models & Execution → Default for new / restarted agents** stores a
verified harness/model profile on the operator's Gateway account. The Gateway
validates the pair against `GET /api/v1/execution/capabilities`, persists the
profile id, and exposes it through both `GET /api/v1/execution/settings` and
`GET /api/v1/execution/routing-preference`. Harness and model must be supplied
together; invalid or unavailable selections are rejected.

The response deliberately reports:

- `applies_to: ["new", "restarted"]`;
- `delivery.automatic: false` and `status: "pending-firstmate-integration"`.

Firstmate does not yet consume this preference automatically. Until that seam
is implemented, an operator or spawning-layer integration must read the
routing-preference endpoint and pass the selected profile to the launch
operation. Saving the preference therefore changes future launch policy only
when that consumer is present; it does not rewrite existing agents.

The composer selector is separate: it passes an explicitly selected profile as
prompt context for a new prompt and does not claim to change a live process.

## Moving a running agent

`MOVE RUNTIME` is a stop-and-relaunch plan, never a seamless live switch. The
operator must confirm the target in the app, then confirm and execute the
request in the Firstmate terminal. In the current release the Gateway records
an idempotent request at
`POST /api/v1/agents/{agent_id}/migration-requests`, but performs no lifecycle
mutation. The UI says this explicitly and remains in `requested` until an
operator reports a transition with terminal evidence.

The recorded plan identifies what the relaunch is intended to preserve:
worktree, checked-out branch, original brief, and recorded progress. The
in-flight turn is not preserved. A terminal integration can report the honest
state sequence `requested → relaunching → running-on-new`, or
`requested/relaunching → failed`; a failed request may be retried, while a
completed migration cannot be reopened.

The transition endpoint requires `terminal_confirmed: true`, the original
idempotency key, and evidence. `running-on-new` is therefore only displayed
when the operator has reported it—not when the request was created or when a
profile was selected.

No Herdr lifecycle command is issued by this feature. Any lifecycle experiment
must use the named, guarded Herdr lab helper contract rather than the captain's
`default` session.
