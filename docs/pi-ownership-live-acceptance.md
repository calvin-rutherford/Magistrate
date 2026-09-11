# Pi semantic ownership live acceptance

Status: **operator checklist; physical iPhone required**. Automated success,
local IPC readiness, and simulator/browser evidence remain **PARTIAL pending
physical-iPhone verification**. Only the authorized post-deployment run below can
support ADOPT. A duplicate dispatch, wrong-turn/native-entry binding,
truncation, terminal replacement, or private block in chat is an immediate
**NO-GO**.

Never paste or print the IPC key, capability, encrypted prompt/dispatch columns,
prompt plaintext from the dispatch ledger, credentials, or native-entry
content. Evidence captures may contain commit IDs, modes/UIDs, fixed state
counts, opaque canonical/Pi IDs, character/byte counts, and hashes. Calculate
content comparisons locally and retain only those bounded values.

## A. Change, backup, and authority gate

1. Confirm the operator is authorized for the production Gateway and dedicated
   captain Pi runtime; identify a second reviewer for the evidence.
2. Record the exact candidate commit with `git rev-parse HEAD` and verify it is
   the intended merged commit, not a dirty deployment-only commit.
3. Record `git status --short`; stop if tracked or untracked release drift is
   present.
4. Record the current deployed commit before update and keep it beside the
   rollout evidence.
5. Confirm `MAGISTRATE_DB_PATH` is absolute, persistent, outside the release
   checkout, a regular non-symlink file, service-owned, and mode `0600`.
6. Take an online SQLite backup, run `PRAGMA integrity_check`, and record the
   exact pre-restart commit in a mode-`0600` companion file.
7. Confirm the backup and companion are in a service-owned mode-`0700`
   directory outside the release checkout.
8. Record the actual deployment identity with `id -u`; do not infer it from an
   example or home-directory name.
9. Require the production environment file to be a service-owned non-symlink
   mode-`0600` file, then review only the presence and names of required Pi
   settings. Never dump the file or systemd environment.
10. Confirm the explicit production flag is `true` and all paths substitute the
    actual UID rather than the literal `<uid>`.

## B. Local trust boundary and extension activation

11. Confirm runtime, key, socket, and journal paths are absolute, normalized,
    distinct, and that key/socket/journal are direct children of the configured
    runtime directory.
12. Confirm no path component is a symlink and no non-sticky replaceable
    group/world-writable component controls the runtime boundary.
13. Confirm the runtime directory owner equals the effective service UID and
    its mode is exactly `0700`.
14. Confirm the key is a regular, single-link, non-symlink file owned by that UID
    with mode exactly `0600`; do not read it.
15. If a journal exists, confirm it is a regular, single-link, non-symlink file
    owned by that UID with mode exactly `0600`; do not read or delete it.
16. Run `npm ci`, `npm run typecheck`, and `npm test` in the deployed
    `pi-extension/` directory and retain command verdicts.
17. Run `pi install "$(pwd)"` from that exact deployed extension directory and
    record the package/install verdict, not any environment contents.
18. Confirm the Gateway unit and dedicated captain Pi unit are distinct. Stop
    if the proposed Pi unit is a Herdr or Firstmate fleet service.
18a. Confirm `pi install` and the dedicated unit use a dedicated service account
    or the same dedicated `PI_CODING_AGENT_DIR`. If another Pi runtime shares
    that package scope, require an explicit false ownership flag in that
    non-captain process before it next starts.
19. Restart only the dedicated captain Pi runtime so installation can actually
    be loaded; do not assume installing changed the old process. Do not restart
    Herdr or Firstmate.
20. Confirm that dedicated unit is active after restart and that its start time
    is newer than the extension installation.
21. Confirm the configured path is a listening Unix socket in `ss -xl`, owned by
    the configured UID, mode `0600`, and not a symlink; require a successful
    signed nonce-bound readiness probe and exact peer PID/unit `MainPID` match,
    not just connectability.
22. Restart only the correct Gateway through its normal guarded lifecycle.
23. Confirm Gateway startup accepted UID/path/key/journal, timeout, same-UID
    peer, and authenticated adapter probe. A startup trust exception is NO-GO.
24. Use the authenticated soak diagnostics and require: `enabled=true`,
    `default_enabled=true`, `defaulted=false` for explicit production config,
    adapter `ready`, and new captain selection `pi-semantic`.
25. Record only fixed prepared/bound/finalized/failed counts and recovery backlog
    before submissions. Do not query encrypted or content-bearing columns.

## C. One exact prepared → bound → finalized turn

26. On the physical iPhone, open captain chat using **Current backend session**;
    do not select a per-request model/profile.
27. Record the canonical conversation baseline and terminal-ingest observation
    counter from authenticated bounded diagnostics.
28. Submit one unique long prompt from the iPhone, including non-ASCII Unicode,
    emoji, line breaks, combining characters, and an unmistakable final marker.
29. Capture that one canonical user ID, turn ID, objective ID, run ID, and
    reserved assistant message ID without capturing dispatch secrets.
30. Immediately verify exactly one dispatch row exists for that turn in
    `prepared` or later state and exactly one canonical user row exists.
31. If observation tooling can pause safely at native bind, prove the same
    dispatch moves `prepared → bound`; otherwise collect timestamped adapter
    state counts/log classifications sufficient to show a bound transition
    occurred before finalization. Do not expose prompt/native content.
32. Verify bound evidence names exactly one Pi session, one Pi user entry, and
    the expected prepare/bind anchors; no second user boundary may be present.
33. Wait for a normal Pi stop and verify the same dispatch moves to `finalized`,
    not a different incarnation or turn.
34. Verify the exact reserved assistant message ID is now the sole canonical
    primary assistant row for the turn and has `content_source=pi-semantic`.
35. Verify the accepted Pi assistant entry is the exact normally stopped final
    entry between bind and finalize anchors. Any later assistant entry inside
    the accepted chain is NO-GO.
36. Verify the turn lifecycle is completed/answered and no intermediate partial
    assistant text was delivered as a separate canonical row.

## D. Exact content, privacy, and iPhone rendering

37. In a controlled local verifier using Pi's read-only native session API,
    concatenate only visible `text` blocks from the exact finalized assistant
    entry in original order. Never print the text.
38. Compute and retain its Unicode scalar/character count, UTF-8 byte count, raw
    SHA-256, and canonical visible-block descriptor SHA-256; retain no content.
39. Compute the corresponding character count, UTF-8 byte count, and raw
    SHA-256 from the one canonical assistant row locally without printing text.
40. Require Pi and canonical character counts, byte counts, and raw hashes to
    match exactly; require the descriptor hash to equal the dispatch ledger's
    accepted `visible_content_sha256`.
41. Confirm the final marker and all expected paragraphs are visible on the
    physical iPhone with no clipped tail, missing Unicode, replacement
    character, normalization change, or collapsed intentional whitespace.
42. Confirm the iPhone shows one user bubble and one assistant bubble for the
    turn—never an append for a growing/reflowed response.
43. Search the rendered chat and canonical payload for the controlled reasoning,
    hook, tool-call, tool-result, retry/error-assistant, telemetry, provider, and
    internal sentinels. Every sentinel must be absent.
44. Confirm only visible text blocks entered chat. Thinking/reasoning, hook,
    tool, result, custom/internal blocks, usage, and model metadata must remain
    excluded.
45. Confirm no Magistrate ID, capability, ownership/correlation metadata, or
    persona/control copy was inserted into the model-visible Pi user text.
46. Compare the terminal-ingest observation counter with the baseline and
    require zero new Herdr reads attributable to this owned turn. Also require
    `eligible_legacy_turns=false` for the isolated acceptance conversation.

## E. Replay, app lifecycle, and restart retention

47. Replay the same HTTP submission identity after finalization and require the
    exact same turn/dispatch/assistant IDs, unchanged message revision/content,
    and no second Pi user entry.
48. Refresh/poll and reconnect WSS repeatedly; require append-or-update by the
    same canonical ID and no duplicate bubble.
49. Background the iPhone app during/after delivery, restore it, and require the
    complete same assistant row and counts/hash.
50. Force-quit the iPhone app, relaunch it, authenticate normally, and require
    the complete same transcript with no local-cache duplicate or truncation.
51. Restart only the named isolated/non-production Herdr lab used for lifecycle
    validation; do not restart shared or production Herdr for this proof. Require
    the already-finalized canonical turn to remain unchanged.
52. Restart Gateway normally, wait for authenticated readiness, and require the
    same owned row, native IDs, final hash, lifecycle, and adapter receipt state.
53. Verify Gateway restart did not increase terminal-ingest observations or
    replace `content_source=pi-semantic`.
54. Verify the adapter journal is absent once all final evidence receipts are
    durable; if it remains, require the bounded recovery backlog to explain it
    and do not rotate/delete anything.

## F. Adapter outage and recovery—no legacy resend

55. In an authorized bounded window, make only the dedicated Pi adapter
    unavailable while leaving Gateway and Herdr state observable. Do not stop
    or alter Firstmate.
56. Submit one new unique iPhone captain prompt and require HTTP 503 only after
    one canonical turn/user row, reservation, and `prepared` dispatch are
    durable.
57. Require no call to `HerdrClient.prompt_agent`, no terminal fallback row, and
    no second provider/model send for that submission.
58. Retry the identical client message ID while unavailable; require the exact
    same turn, dispatch incarnation, capability hash, and reservation, with no
    duplicate canonical row.
59. Restart the same dedicated Pi adapter/session, prove the socket ready, and
    let bounded Gateway recovery converge without resubmitting through Herdr.
60. Require that dispatch to bind/finalize once, update only its reserved row,
    and match Pi/canonical counts and hashes exactly.
61. Simulate/observe a lost final acknowledgement only through the guarded test
    seam; require final replay/receipt convergence and one canonical revision,
    not duplicate content.
62. Confirm Herdr absence or restart during semantic recovery has no effect on
    ownership state or final canonical content.

## G. Voice, rollback readiness, and verdict

63. Verify chat microphone transcription merely fills the composer; its
    explicit send uses `/api/v1/captain/prompt` and passes the same semantic
    checks above.
64. Open continuous Voice Mode while ownership is enabled and require the legacy
    `/api/v1/voice/moves` route to fail explicitly with HTTP 503 before any
    Herdr dispatch or canonical turn creation. Do not report voice parity.
65. Confirm compatibility disablement would not release either accepted owned
    turn: both remain in ownership counts and terminal-ineligible.
66. Before any rollback, stop submissions and require zero prepared/bound rows,
    zero unacknowledged finalized/failed rows, and no journal. Otherwise do not
    disable or rotate the channel.
67. Review all evidence for secrets/content accidentally captured; destroy and
    recollect unsafe evidence rather than filing it.
68. Record **ADOPT** only if every physical-iPhone and outage/restart check
    passes. Record **PARTIAL** if automation/preflight passed but live evidence
    is incomplete. Record **NO-GO** immediately for any duplicate dispatch,
    wrong-turn binding, truncation, terminal replacement, secret/private block,
    or fallback of an owned turn.
