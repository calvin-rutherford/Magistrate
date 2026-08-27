# Production deployment

The production gateway and its static Expo web build live together in
`/home/spectre/firstmate/projects/Magistrate-deploy`. The user unit's
`WorkingDirectory`, `EnvironmentFile`, and `MAGISTRATE_DIST_DIR` all point at
that persistent checkout. The deployment checkout owns its `.env`; keep that
file out of Git and do not copy it from a dirty development checkout during a
routine update.

Run `scripts/deploy_magistrate.sh` from a trusted shell for an update. The
script fetches `origin/main`, refuses dirty or divergent checkouts, performs a
fast-forward-only update, runs the supported `npx expo export -p web` build,
restarts `magistrate-gateway.service`, and verifies the service is active. A
timer is intentionally not installed: deployment requires a clean checkout,
available dependencies, and a successful frontend build, so an unattended
pull could leave the running service and assets out of sync.
