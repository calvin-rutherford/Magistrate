# Production deployment

The production gateway and its static Expo web build live together in
`/home/spectre/firstmate/projects/Magistrate-deploy`. The user unit's
`WorkingDirectory`, `EnvironmentFile`, and `MAGISTRATE_DIST_DIR` all point at
that persistent checkout. The deployment checkout owns its `.env`; keep that
file out of Git and do not copy it from a dirty development checkout during a
routine update.

Run `scripts/deploy_magistrate.sh` from a trusted shell for a manual update. The
script fetches `origin/main`, refuses dirty or divergent checkouts, performs a
fast-forward-only update, runs the supported `npx expo export -p web` build,
checks that `index.html`, `chat.html`, and `voice.html` exist, restarts
`magistrate-gateway.service`, and verifies that the HTTP process is reachable.
It never resets, stashes, or discards deployment-only commits.

## Automatic demo redeploy

`.github/workflows/deploy-demo.yml` runs on every push to `main` (the signal
GitHub emits after a pull request is merged). Configure these repository Actions
secrets before enabling it:

- `MAGISTRATE_DEPLOY_HOST` — the demo host name or Tailscale address
- `MAGISTRATE_DEPLOY_USER` — the unprivileged service account
- `MAGISTRATE_DEPLOY_SSH_KEY` — a dedicated SSH private key authorized only for
  the deployment account
- `MAGISTRATE_DEPLOY_KNOWN_HOSTS` — the pinned `known_hosts` entry

The workflow uses only those repository-provided host credentials and invokes
the guarded script on the persistent checkout. Missing secrets, unavailable host
access, a dirty checkout, or a non-fast-forward/divergent checkout fail closed;
no reset or force push is attempted. The workflow's concurrency group prevents
overlapping updates.

If automatic deployment is unavailable, log into the demo host through the
approved SSH path and run:

```sh
cd /home/spectre/firstmate/projects/Magistrate-deploy
git status --short
/home/spectre/firstmate/projects/Magistrate-deploy/scripts/deploy_magistrate.sh
```

Resolve any reported dirty/divergent state by preserving and reviewing its
unique work, then retry. Do not use `git reset --hard`, `git stash`, or a force
push. If host credentials are unavailable, the remaining manual step is to have
the deployment owner configure the four repository secrets (or run the command
from a trusted host) and then verify the configured HTTPS gateway health URL
with an issued Bearer session. Do not put the deployment host, runner address,
or bootstrap secret in Git.
