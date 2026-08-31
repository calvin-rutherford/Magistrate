#!/usr/bin/env bash
set -euo pipefail

# Repository invariant: demo deployment may be deliberately dispatched, but a
# repository event (especially a merge push to main) must never start SSH.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
WORKFLOW="$ROOT/.github/workflows/deploy-demo.yml"

if [[ ! -f "$WORKFLOW" ]]; then
  echo "deployment workflow not found: $WORKFLOW" >&2
  exit 1
fi

# Keep this check anchored to top-level workflow events. A push trigger nested
# under `on` would make ordinary merges deploy even if its branch filter changed.
if grep -Eq '^[[:space:]]{2}(push|schedule|workflow_run|repository_dispatch|workflow_call):' "$WORKFLOW"; then
  echo 'invariant failed: repository events can trigger demo deployment' >&2
  exit 1
fi

grep -Eq '^  workflow_dispatch:' "$WORKFLOW" || {
  echo 'invariant failed: demo workflow is not manually dispatchable' >&2
  exit 1
}
grep -Eq '^    inputs:' "$WORKFLOW" || {
  echo 'invariant failed: manual deployment lacks a confirmation gate' >&2
  exit 1
}
grep -Eq '^      confirm:' "$WORKFLOW" || {
  echo 'invariant failed: manual deployment confirmation input is missing' >&2
  exit 1
}
grep -Fq 'if: ${{ inputs.confirm == true }}' "$WORKFLOW" || {
  echo 'invariant failed: deployment job is not gated by manual confirmation' >&2
  exit 1
}

grep -Fq "'\$DEPLOY_DIR/scripts/deploy_magistrate.sh'" "$WORKFLOW" || {
  echo 'invariant failed: manual workflow no longer uses the guarded deploy script' >&2
  exit 1
}

# Deliberate manual dispatches retain the existing credential contract. This
# test checks names only; it never reads or creates repository secret values.
for secret in \
  MAGISTRATE_DEPLOY_HOST \
  MAGISTRATE_DEPLOY_USER \
  MAGISTRATE_DEPLOY_SSH_KEY \
  MAGISTRATE_DEPLOY_KNOWN_HOSTS; do
  grep -Fq "secrets.$secret" "$WORKFLOW" || {
    echo "invariant failed: manual workflow lost required secret reference $secret" >&2
    exit 1
  }
done

echo 'deployment workflow manual-only invariant passed'
