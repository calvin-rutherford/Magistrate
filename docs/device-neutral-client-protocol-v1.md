# Device-neutral client and artifact protocol v1

## Status and truthful boundary

Magistrate has one authenticated Gateway state model for iPhone, web, desktop,
headsets/AR, wearables, gaming inputs, assistive devices, and future subvocal or
neural adapters. A device class is presentation metadata, never a second
identity, project, memory, routing, billing, execution, Fleet, Activity, or
Attention authority.

Today the shipped product has iPhone/web text, foreground microphone capture,
photo library, camera, document upload, and the existing responsive output
renderer. `magistrate.perception-event.v1` is implemented as an authenticated
normalization and consent boundary. It does **not** claim ambient listening, an
AR SDK integration, a wearable integration, a subvocal sensor, a neural
interface, thought reading, or autonomous intent execution. Those require new
input adapters and platform/privacy review.

## Stable topology

```text
input adapter -> POST /api/v1/perception/events -> normalized draft
                                               -> explicit confirmation (when required)
ordinary Chat/Voice adapter -> /api/v1/magi/* -> canonical Magi conversation
execution events -> structured runtime -> Fleet / Activity / Attention renderers
private object store -> opaque artifact id -> owner-authenticated signed access
```

All paths use the same server-issued principal. A client cannot submit a
principal, tenant, provider credential, billing identity, or execution scope in
a perception event. Projects and conversation IDs are context references only;
they cannot grant access. Adding an adapter or output renderer must not modify
authentication, authorization scopes, Native Chat ownership, project policy,
model routing, usage accounting, execution intake, or structured runtime
projection.

## Perception event

`POST /api/v1/perception/events` accepts a strict, bounded JSON object:

- `schema_version`: exactly `magistrate.perception-event.v1`;
- `event_id`: adapter-generated idempotency key;
- `client`: opaque installation/client ID, device class, adapter ID and adapter
  protocol version;
- `modality`: text, voice, image, gesture, ambient, spatial, subvocal, or neural;
- `observed_at_ms`: source observation time within the accepted replay window;
- `context`: optional project, Native Chat conversation, and UI surface refs;
- `confidence`: finite 0–1 adapter confidence;
- `consent`: explicit capture, purpose, bounded retention, and (where needed)
  biometric-processing consent;
- `artifact_ref`: opaque owner-scoped upload ID for byte-bearing modalities;
- `intent`: normalized kind/impact plus transcript/transform provenance.

Unknown fields and unknown schema versions fail closed. Reusing an event ID
with different bytes is a conflict. Responses include the authenticated
principal, normalized event, expiry, revision, and authorization state. They
always include `executes_action: false`: perception ingress never dispatches an
action.

Confidence below `0.75`, every high-impact intent, and every neural/subvocal
observation requires a separate authenticated confirmation. Confirmation
requires `command` scope and exact event ID/revision. Even after confirmation,
the protocol only records a confirmed draft; an existing governed product API
must separately authorize and perform any action. Consequently uncertain
neural or “thought” input can never alone authorize a high-impact action.

The portable TypeScript types and local fail-safe live in
`frontend/src/protocol/PerceptionProtocol.ts`. Gateway authority and persistence
live in `gateway/app/perception.py`.

## Attachments and artifacts

The composer supports up to 10 screenshots/photos/camera images, PDFs, UTF-8
text/code files, archives, and common Office documents, with a 25 MiB per-file
and 50 MiB per-message cap. It previews images/files, allows removal before
send, exposes observed upload lifecycle progress, retains failures for retry,
and sends only server-confirmed `stored` records.

`gateway/app/uploads.py` is the storage authority:

- names are sanitized display metadata and never storage keys;
- MIME is sniffed from signatures/UTF-8 structure; extensions and client MIME
  are not trusted;
- random opaque object keys are containment-checked under a mode-0700 private
  root, while object files are mode 0600;
- upload/message lookup always includes authenticated owner identity;
- SHA-256 is verified before provider routing;
- `MAGISTRATE_UPLOAD_SCAN_COMMAND` is an optional no-shell malware/content scan
  hook (exit 0 clean, 1 rejected, all other outcomes unavailable/fail closed);
- unattached and attached retention are bounded by
  `MAGISTRATE_UNATTACHED_UPLOAD_TTL_SECONDS` and
  `MAGISTRATE_ATTACHED_UPLOAD_TTL_SECONDS`; a perception event atomically keeps
  its owner-scoped artifact through the event's consented expiry, and cleanup
  tombstones expired rows and removes contained objects;
- short-lived access URLs are HMAC-signed with a dedicated
  `MAGISTRATE_UPLOAD_SIGNING_KEY` (or the persistent Gateway secret), remain
  authenticated, and are owner-bound;
- API and product records expose opaque IDs/URLs, never filesystem paths.

The OpenAI Responses adapter receives current-turn images as `input_image` and
other admitted documents as `input_file`, loaded only after owner and digest
validation. Attachment-bearing turns deliberately receive no execution tools,
so instructions embedded in a document cannot become action authority. The
provider-neutral model boundary carries bytes plus sniffed MIME, not paths. A
future provider must explicitly implement equivalent input parts
or reject the attachment; metadata-only processing must not be presented as
file understanding.

Execution output remains typed product evidence (`pull-request`, `report`, or
`commit`) in `firstmate.completion-evidence.v1`. The public structured runtime
projects URLs or opaque references only. Report references are bounded IDs and
cannot be filesystem paths. A producer that needs downloadable output must
first place it in the private artifact store and emit its opaque product
reference; raw runner paths are not a product contract.

## Compatibility and retention tests

Gateway tests cover MIME spoofing, path-safe names, owner isolation, signed
access, scanner rejection, object deletion/expiry, provider file encoding,
strict protocol versioning/idempotency, consent, and low-confidence/neural
confirmation. Frontend typecheck covers the shared adapter contract. Existing
Native Chat and browser suites cover preview/remove/send/retry and truthful
server-issued upload state.
