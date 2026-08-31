# DEAT-001 — Deskless Operator Alpha device acceptance

**State:** planned / not executed in this repository slice

This is the named acceptance artifact for the first trusted owner/operator
native foundation. It must be completed with observed evidence; placeholders
must not be replaced with assumptions from web tests or an Expo export.

## Objective

Prove that a trusted owner can install a signed native build on a physical
iPhone, authenticate through the public HTTPS/WSS Gateway, preserve a secure
short-lived session, route exact pending targets, receive honest notification
fallback/push behavior, and use foreground Voice Mode without exposing the
private execution host or credentials.

## Run record

| Field | Recorded value |
|---|---|
| Revision / commit | **Not executed** |
| Device model | **Not recorded** |
| iOS version | **Not recorded** |
| Build profile / number | **Not recorded** |
| EAS project / archive | **Not recorded** |
| Gateway revision | **Not recorded** |
| Network (Wi-Fi/cellular) | **Not recorded** |
| Start timestamp (UTC) | **Not recorded** |
| End timestamp (UTC) | **Not recorded** |
| Elapsed time | **Not recorded** |
| Interventions | **Not recorded** |
| Outcome | **Not executed** |
| Verification evidence | **None** |

## Test plan

Record pass/fail and evidence for each case:

1. EAS development build installs on a physical iPhone; the app is not Expo
   Go or simulator-only.
2. Fresh, wrong, valid, expired, revoked, and logged-out sessions keep
   protected routes closed until server validation and do not leave bearer
   values in AsyncStorage.
3. The configured cellular and Wi-Fi endpoint is HTTPS; the events socket is
   WSS; no localhost, private runner address, bootstrap, provider, or harness
   credential appears in the archive or logs.
4. Cold, background, terminated, and warm launches route valid voice,
   attention, agent, and PR intents exactly once; malformed/external/duplicate
   intents fail safely; unauthenticated intents survive the auth gate.
5. Native notification permission, Expo token registration, Gateway delivery,
   denied permission, offline/provider failure, and in-app fallback are
   observed separately. A foreground poll is not recorded as server push.
6. Voice permission denial, foreground start/stop, final transcription, TTS,
   cancellation, tap interruption, and background/lock behavior are recorded
   without claiming ambient listening, VAD, Siri, or Action Button support.

## Evidence attachments

Attach the signed build identifier, device screenshots, Gateway request/log
redactions, push provider response/receipt, and any defect IDs here after the
run. Do not attach secrets or raw bearer tokens.
