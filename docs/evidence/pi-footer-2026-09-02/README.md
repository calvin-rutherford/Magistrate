# Live Pi footer / captain-chat evidence — 2026-09-02

This package was captured before editing from the deployed Gateway and the live
Firstmate Pi pane. The bearer token is intentionally not retained.

## Exact artifacts

- `gateway/tests/fixtures/production-pi-firstmate-current-full.ansi` — exact
  94,377-byte ANSI Herdr read (`recent-unwrapped`, maximum retained lines).
- `gateway/tests/fixtures/production-pi-footer-no-ch-current.ansi` — exact ANSI
  rows 541–543 extracted byte-for-byte from that capture; Pi wrapped the current
  no-`CH` footer over two rows inside a background box.
- `parse-agent-history.before.json` and
  `classify-history-rows.before.json` — complete outputs from unmodified main.
- `sqlite-live-hello-{before,after}-get.json` — canonical turn before and after
  the ingesting GET.
- `http-live-hello.json` — exact target-turn subset of
  `GET /api/v1/conversations/captain/messages`.
- `sqlite-poisoned-no-ch-row.json` and `http-affected-turns.json` — the existing
  metadata-only canonical reply and its delivered HTTP context.

The live submission id was `pi-footer-live-1788313923`; its pane response was
`Hello, captain.`. Unmodified main parsed and classified both rows, the GET
stored the response once as `cm_f3e9efef621fafea379d`, and the HTTP payload
returned that same identity once.

## Diagnosis

**Initiating trigger:** Pi emitted the structurally complete no-`CH` footer
`↑… ↓… R… $… (sub) …%/… (auto) gpt-…`. The legacy artifact branch only
recognized arrow rows containing `CH`, so the no-`CH` variant remained
conversation.

**Masking conditions / disconfirming evidence:** by the reproduction read, the
live bottom footer had changed back to the historical `CH98.9%` form. The new
`Hello` therefore succeeded end to end. This disproves a canonical HTTP
selection or frontend reconciliation defect for that turn. The earlier no-`CH`
observation nevertheless persisted as visible assistant row
`cm_0b0b11ba0258c69589f1` for `Scratch that`, proving the classifier hole and
showing why a future-only filter is insufficient.

**Visible symptom:** the metadata-only terminal row was delivered as an
assistant `conversation` message in the canonical HTTP payload instead of
Firstmate prose.

## Independent prose-loss boundary

The smallest counterfactual uses the exact boxed no-`CH` rows between a real
captain prompt and real assistant prose. Before the fix, an unrecognized boxed
footer inherited Pi's user role. `build_segments()` therefore treated it as a
new prompt/audience boundary; `_match_segments_to_turns()` could not match that
telemetry prompt to a canonical turn, and the following legitimate assistant
segment was discarded **between classification and canonical storage**. It was
never an API selection or browser rendering loss.

The corrected parser recognizes the complete footer grammar before assigning a
box role and emits `assistant/control`. Agent-side control does not close the
captain segment, so the following prose reaches the primary canonical slot
exactly once. The Gateway and frontend parser tests run the same counterfactual.

## Poison repair boundary

Canonical cleanup examines only assistant primary rows with
`source='terminal'` and deletes a row only when the entire text satisfies the
new anchored Pi footer grammar. It does not use similarity, model-name
searches, or substring trimming. Corrected retained rows are then re-ingested
through ordinary prompt/segment matching. Mixed or conversational prose is not
rewritten.
