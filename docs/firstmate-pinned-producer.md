# Pinned Firstmate semantic producer

Magistrate consumes `fm-captain-event.v1` as source-native canonical activity. It does not derive autonomous conversation turns from those events. The reviewed producer is not present in every Firstmate release, so its executable code is installed separately from Firstmate's operational state and is opt-in until an operator completes activation.

## Immutable contract

`runtime/firstmate-producer.lock.json` allows exactly:

- source: `https://github.com/calvin-rutherford/firstmate.git`
- commit: `2af0d17014cb2e244aa441bfe6df16c4f630475b`
- tree: `146c55db1fd4d9cb2edf38d384b4d2362d2041e3`
- reviewed producer, snapshot, spawn, and Pi extension artifact hashes

The installer fetches the locked commit directly from that single source, checks the Git tree/source/cleanliness and every runtime artifact, and only then renames the staged checkout into place. It never edits or treats `FM_HOME` as code. A failed install leaves the prior root and activation untouched.

## Install and validate

Run as the same Unix account that runs Gateway and Firstmate:

```bash
export FM_HOME=/var/lib/firstmate
export PRODUCER_ROOT=/var/lib/magistrate-code/firstmate-producer/2af0d17014cb2e244aa441bfe6df16c4f630475b
scripts/install_firstmate_producer.sh install --root "$PRODUCER_ROOT"
scripts/install_firstmate_producer.sh verify --root "$PRODUCER_ROOT"
```

The destination and `FM_HOME` must be distinct absolute, non-symlink, root- or service-owned trees without unsafe world-writable components. Installation requires network access to the single locked public repository; it has no patch fallback.

## Activate (Firstmate-owner action)

Do not assume the developer session's Firstmate home is the deployed runtime home. Read `FM_HOME` from the deployed Gateway service environment, and treat that value as operational state only. The current development Firstmate session under `/home/spectre/firstmate` remains unmanaged and must not be edited or reloaded in place as part of this install.

The Pi extension is loaded at session start. **Before activation, the Firstmate owner must identify the sessions attached to that deployed runtime home, quiesce applicable work, and restart every applicable captain/worker Pi session through the pinned Firstmate root with `FM_ROOT_OVERRIDE=$PRODUCER_ROOT`.** Do not infer that sessions reloaded merely because installation succeeded, and do not assert the activation flag while any applicable session still runs an older extension. Existing durable outbox state is retained.

After confirming all applicable sessions loaded the pin:

```bash
scripts/install_firstmate_producer.sh activate \
  --root "$PRODUCER_ROOT" \
  --fm-home "$FM_HOME" \
  --sessions-reloaded
scripts/install_firstmate_producer.sh ready \
  --root "$PRODUCER_ROOT" \
  --fm-home "$FM_HOME"
```

Activation writes the exact home-local `config/captain-event-outbox` opt-in atomically, then recovers and validates the durable producer journal. Validation failure removes the newly written flag. Configure Gateway only after `ready` succeeds:

```dotenv
FM_HOME=/var/lib/firstmate
MAGISTRATE_FIRSTMATE_ROOT=/var/lib/magistrate-code/firstmate-producer/2af0d17014cb2e244aa441bfe6df16c4f630475b
MAGISTRATE_FIRSTMATE_CAPTAIN_REQUIRED=true
```

With required mode on, deployment and Gateway startup fail closed if the pin is absent, modified, aliased to `FM_HOME`, inactive, or has invalid producer state. With the flag off, existing installations keep working; a configured-but-invalid root is reported unavailable rather than falling back to executable files under `FM_HOME`.

Authenticated `/api/v1/diagnostics/soak` includes bounded `firstmate_producer` readiness (`ready`, `installed-inactive`, `not-configured`, or an explicit failure class), the required flag, activation boolean, and expected commit. It includes neither filesystem paths nor outbox payloads. Canonical source diagnostics continue to expose per-principal cursor/tail/lag and bounded error classes.

## Exact guarded post-merge sequence

The captain has standing merge and guarded-deploy authority for this program; this implementation worker does not exercise either. After the PR is green and merged, the deployment/Firstmate owners run:

```bash
set -euo pipefail
set +x
export MAGISTRATE_DEPLOY_DIR="${MAGISTRATE_DEPLOY_DIR:-/home/spectre/firstmate/projects/Magistrate-deploy}"
export PRODUCER_ROOT="$HOME/.local/share/magistrate/firstmate-producer/2af0d17014cb2e244aa441bfe6df16c4f630475b"
"$MAGISTRATE_DEPLOY_DIR/scripts/install_firstmate_producer.sh" install --root "$PRODUCER_ROOT"
"$MAGISTRATE_DEPLOY_DIR/scripts/install_firstmate_producer.sh" verify --root "$PRODUCER_ROOT"
```

The Firstmate owner then obtains the deployed service's exact `FM_HOME` from its protected environment (without printing that environment), confirms it is distinct from `PRODUCER_ROOT`, and reloads the applicable deployed-runtime Pi sessions onto the pin. **If that confirmation has not happened, stop here; do not activate or deploy required mode.** Once confirmed:

```bash
export DEPLOYED_FM_HOME='<exact FM_HOME from deployed service environment>'
test -d "$DEPLOYED_FM_HOME"
test "$DEPLOYED_FM_HOME" != "$PRODUCER_ROOT"
"$MAGISTRATE_DEPLOY_DIR/scripts/install_firstmate_producer.sh" activate \
  --root "$PRODUCER_ROOT" --fm-home "$DEPLOYED_FM_HOME" --sessions-reloaded
"$MAGISTRATE_DEPLOY_DIR/scripts/install_firstmate_producer.sh" ready \
  --root "$PRODUCER_ROOT" --fm-home "$DEPLOYED_FM_HOME"
```

Set these protected Gateway environment values (do not modify the development Firstmate checkout):

```dotenv
FM_HOME=<the existing deployed runtime value>
MAGISTRATE_FIRSTMATE_ROOT=<PRODUCER_ROOT above>
MAGISTRATE_FIRSTMATE_CAPTAIN_REQUIRED=true
```

Then invoke the repository's existing guarded deployment workflow/script and require its normal green readiness/authenticated smoke results. The deploy preflight reruns immutable `verify` and `ready` before build/restart. Installation or activation alone is never evidence that the Gateway deployed.

## Roll back

First set `MAGISTRATE_FIRSTMATE_CAPTAIN_REQUIRED=false` and remove `MAGISTRATE_FIRSTMATE_ROOT` from Gateway's environment, then deploy/restart Gateway. Quiesce or restart Pi sessions onto the prior Firstmate root. Finally preserve and validate the durable journal while removing only activation:

```bash
scripts/install_firstmate_producer.sh deactivate \
  --root "$PRODUCER_ROOT" \
  --fm-home "$FM_HOME"
```

Do not delete `state/captain-events/`; it is the replayable source journal and contains the post-commit Magistrate acknowledgement. The immutable code directory can be retained for audit and removed later only while inactive.

## Compatibility gate

Gateway CI runs the installer against the public immutable source and sets `MAGISTRATE_TEST_PINNED_FIRSTMATE_ROOT`. The real Pi `turn_end` extension then executes twelve harmless semantic operations, including a failure-after-start retry and a crash-after-pending recovery. Gateway tests prove exact outbox ingestion, stable source identities, tenant-qualified canonical IDs, restart/duplicate behavior, transactional acknowledgement, mutable decision/completion revisions, replay cursors, prose redaction, and the absence of invented conversation turns.
