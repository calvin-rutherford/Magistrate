#!/usr/bin/env bash
# Retired live rsync watcher: it copied unreviewed code and local secrets.
echo 'Live rsync deployment is retired. Use the clean-commit deployment and backup gates in scripts/deploy_magistrate.sh.' >&2
exit 64
