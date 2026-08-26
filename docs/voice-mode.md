# Voice Mode deployment notes

Voice is a standalone `/voice` command surface. It does not route transcripts to Chat and does not include wake-word, always-listening, or full-duplex behavior.

## Required runtime configuration

- Set `MAGISTRATE_TOKEN` on the gateway. The gateway fails closed when it is absent and accepts the standard `Authorization: Bearer` form (the legacy header is retained for existing non-Voice clients).
- Set `EXPO_PUBLIC_GATEWAY_URL` for the deployed gateway and provide `EXPO_PUBLIC_MAGISTRATE_TOKEN` through the deployment’s authenticated runtime configuration. No default credential is present in source or the gateway.
- Raw recordings are created in the platform cache and deleted by the Voice client after each transcription attempt. The configured speech provider’s retention, region, and zero-data-retention policy must still be reviewed before deployment.

## Result semantics

Voice returns an immediate, actor/session-bound acknowledgement and polls `GET /api/v1/voice/moves/{move_id}` for a bounded period. The current Herdr CLI exposes submission acknowledgement but no authenticated run-completion event, so an `acknowledged` result remains visibly pending rather than being presented as agent completion. When Herdr provides a correlated completion event, this endpoint is the integration point for consuming it.

Focused checks:

```sh
cd gateway && PYTHONPATH=. .venv/bin/pytest -q
cd frontend && npx tsc --noEmit && npm run test:voice
cd frontend && npx expo export --platform web
```
