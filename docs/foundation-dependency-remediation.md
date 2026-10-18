# Foundation dependency qualification — 2026-10-18

Scope: `fm/magistrate-contract-deps-p2`, based on current `origin/main`
`23b10ce1da0cccbc37239804e3b504d72f6df95e`. The preceding five merged changes
remain ancestors. This is a **review candidate**, not deployment, producer
activation, live-data access, paid work, iOS compilation or phone acceptance.
Independent integration review of the security-sensitive artifacts is required.

## Advisory evidence and choice

Fresh `npm ci`, `npm explain braces micromatch node-forge --json` and
`npm audit --audit-level=low --json` reproduced **20 high**, exit 1. Current
official GitHub advisories and upstream/registry releases were queried again,
not inferred from the earlier portfolio report:

- [GHSA-vfj7-8cjw-p6xm](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm),
  `braces <=3.0.3`, no patched release. Root paths: both unscoped Metro maps,
  `patch-package → find-yarn-workspace-root → micromatch`. The already-landed
  `@expo/metro-file-map@57.0.4` fixed only its own edge.
- [GHSA-86w9-cpqp-85rv](https://github.com/advisories/GHSA-86w9-cpqp-85rv),
  `node-forge <=1.4.0`, no patched release. Both Expo CLI's iOS identity reader
  and its `@expo/code-signing-certificates` consumer retain Forge.
- [GHSA-8pqf-f4m5-798g](https://github.com/advisories/GHSA-8pqf-f4m5-798g),
  `Twisted <=26.4.0`, no patched release. Independently scanning retained backend
  requirements exposed `Daphne 4.2.3 → Twisted 26.4.0`. The bounded lab ASGI
  replacement was explicitly approved before implementation. IMAP's lack of
  product reachability is not an advisory waiver.

A complete installed-byte scan additionally found **bundled braces in
`resolve-workspace-root@2.0.1`**, invisible as an npm dependency edge. A zero
npm result before removing this bundle was explicitly **not** accepted.

The independent Compose-root scan found stale Django, DRF, protobuf, pytest and
python-dotenv pins. Qualified updates are Django **5.2.17** (maintained 5.2 LTS),
DRF **3.17.2**, protobuf **5.29.6**, pytest **9.0.3**, dotenv **1.2.2**. The retained
Google adapter moves **0.5.4 → 0.8.6** to permit the patched protobuf line, without
changing its API or executing a provider request. `libtmux` is now explicitly
declared in that graph because the retained service imports it. No unrelated
framework downgrade, risk suppression or optional audit was used.

## Reproducible consumer removal, not renamed vulnerable packages

`frontend/security/sources.json` is the source/provenance manifest:
registry tarball URL, exact upstream Git revision, SHA-256 and registry SHA-512,
license, modification patch hash, overlay hashes and removed files.
`artifacts.json` lists the complete resulting file inventory and hashes.
Artifacts are deterministically packed into SHA-256-named `.tgz` files; their
**original names and versions are deliberately retained**, and SOURCE.json
travels inside every modified consumer. They must never be published as upstream
packages. These are modified consumers, **not** allegedly patched Forge/braces.

| Source-derived consumer | Exact change / qualification |
|---|---|
| `metro-file-map@0.84.4` and `0.84.5` | Backport both official Metro changes [a779246](https://github.com/react/metro/commit/a77924694dd4e36c0a3e595956e322033a6c2c57) and [3865c00](https://github.com/react/metro/commit/3865c007c592c4ab7357f95fa6ae9e6ac735a7d1): explicit suffix/basename/prefix matching, including dotfiles/multipart suffixes, across JS, Flow and TS declarations. No micromatch dependency. |
| `find-yarn-workspace-root@2.0.0` | Preserve ordered micromatch list selection (exclusion/re-inclusion, only-negatives, extglobs), using maintained `picomatch@4.0.7`; reject overlong/deep declarations rather than overflow. Preserve traversal and nearest workspace exclusion, retain patch-package. |
| `resolve-workspace-root@2.0.1` | Unbundle exact upstream `72f675ae9ea952e832c63f1138346406b0d8630b` sources with the same matcher. Preserve sync/async, pnpm/package priority, options, invalid configuration behavior and ancestor traversal. `js-yaml` is a visible locked dependency, not a stale hidden bundle. |
| `@expo/code-signing-certificates@0.0.6` | Remove Forge edge and its stale compiled tests/maps. Node OpenSSL/WebCrypto owns RSA keys, signing and verification; maintained `@peculiar/x509@2.2.0` owns certificate/CSR encoding. Explicit `reflect-metadata@0.2.2`; no handwritten ASN.1/RSA parser or Forge fork. |
| `@expo/cli@57.0.27` | Remove direct Forge edge; adapt iOS CN/O/OU extraction to X.509 APIs and await CSR creation. Delete source maps of changed consumers, not only their dependency entries. |

Expo **57**, RN **0.86.3**, scoped Metro map **57.0.4**, both retained unscoped
Metro versions and all prior image-size/query-string/uuid safeguards remain.
The exact parent-scoped override still installs **image-size 2.0.4**; the original
`patch-package --error-on-fail` bridges remain mandatory. No lockfile was deleted.
`security/update_lock.py` preserves existing unrelated resolutions, asks npm to
resolve the manifests in a temporary worktree-local fixture, and normalizes only
local artifact paths. It works around npm 10's initial relative override path
resolution; `npm ci` consumes the resulting relative paths normally. `npm ls
--all` must also pass; a lockfile-only audit is insufficient.

The X.509 release evidence is [Peculiar v2.2.0](https://github.com/PeculiarVentures/x509/releases/tag/v2.2.0)
(2026-10-15), which carries bounded external BER parsing and fixes limits applied
to self-generated DER. Inputs here set depth/node/size bounds. Certificates reject
multiple PEM blocks, trailing bytes and duplicate extensions. RSA is at least
2048 bits, SHA-256 with PKCS#1 v1.5; key-pair consistency, signature, validity and
code-signing usages are checked. CSR signatures are verified before issuance;
development certificates preserve the Expo project-information OID and cap
validity at the issuer's expiration. Creation APIs are now asynchronous, and the
retained CLI call site is adapted/tested. This is a scoped consumer API migration,
not general drop-in compatibility with Forge's object model.

### Reproduce and review

On Linux, Python 3.12, Node 22 (tested 22.23.2), npm 10.9.8, POSIX `patch` and
OpenSSL CLI, from a clean checkout:

```sh
(cd frontend && npm ci)
python3 frontend/security/build_artifacts.py --output .release/reproduced --check
python3 frontend/security/qualify_controls.py
(cd frontend && npm run test:release && npm ls --all)
node frontend/security/sbom.cjs > .release/frontend-sbom.json
(cd frontend && npm audit --audit-level=low --json > ../.release/frontend-audit.json)
```

`build_artifacts.py` downloads hash-pinned sources, rejects unsafe archive members,
applies exact patches with zero fuzz, verifies identity/licenses and builds byte-
identical archives. Patches use zero-context unified diffs over hash-verified
upstream bytes, avoiding whitespace-only context lines in committed patch files;
normalizing that representation was checked to change no installed implementation
bytes (only `SOURCE.json` provenance and resulting archive hashes). No build secrets or upstream lifecycle scripts are used.
`verify-install.cjs` runs before patch-package on every install: checks archive,
lock and every installed consumer file, rejects extra files/edges, and searches all
installed JS/maps/JSON (including nested bundles) for original implementation
markers. Marker searches are useful negative evidence, not a complete malware
scanner. All six full file inventories provide stronger drift detection for the
modified consumers. Integrity tests deliberately corrupt source, add a bundle,
reintroduce an edge and make the existing Metro patch fail; install must fail.

`qualify_controls.py` fetches exact hash-pinned original packages into a temporary
ignored **test-only** fixture and removes it afterwards. These known-vulnerable
controls are never application dependencies or vendored artifact bytes. It proves:

- Original braces 3.0.3 throws stack-exhaustion `RangeError` on 4,500 nested braces
  in a timeout-bound child. The new matcher rejects excessive nesting explicitly.
- Original Forge 1.4.0 accepts a signature with an extra DigestAlgorithm child;
  Node and OpenSSL CLI reject it. The fixture uses its own synthetic private key
  to construct the malformed signature; it is an acceptance-bug control, not a
  claim of an end-to-end production exploit.
- 8,064 ordered-matcher and 2,331 original bundled-resolver comparisons have zero
  differences. Positive/exclusion/re-inclusion/extglob/dotfile/option cases are
  also in the installed consumer suite.

Installed compatibility covers **both exact unscoped map versions**, real fallback
filesystem add/change/delete, native handler filtering and Watchman protocol
fixtures. Linux tests do not claim macOS FSEvents or a live Watchman daemon.
All helper signing/PEM/CSR APIs, Unicode manifest bytes, self-signed and issued
certificates use a separate OpenSSL command-line standards oracle. Wrong keys,
validity, missing usages, tampered signatures/CSR and malformed PEM fail. CLI
project signing, mocked Expo Go online CSR/chain/cache/offline flows, and iOS
identity extraction are tested without a keychain, Apple identity or paid API.
Full Metro web export/browser suites remain required beyond these fixtures.

Original upstream licenses are preserved in every artifact, plus the original
Metro MIT license (absent from its published tarball) and micromatch MIT notice
for the specialized ordered-selector logic. The full installed CycloneDX SBOM
keeps package identities, artifact SHA-512 and distribution paths, adding explicit
modification/source/patch hashes via `security/sbom.cjs`. It includes maintained
X.509/ASN.1/picomatch/yaml dependencies, not just the six modified consumers.
Review exact hashes in the committed manifests and the audit evidence index.

### Independent review correction: whole-input CSR consumption

Artifact review found that candidate `86fa67a` accepted a valid CSR DER value
followed by `00` or `0500`, unlike the pinned original Forge. The reproduction
also accepted end-of-content/empty-sequence/integer suffixes, arbitrary bytes,
encoded whitespace, a second CSR and zero padding. This was a real parser-boundary
regression despite the clean advisory result; the earlier candidate is superseded.

`convertCSRPEMToCSR` now requires successful, **whole-input** consumption from the
same maintained, resource-bounded `asn1js@3.1.0` decoder before X.509 schema parsing.
Comparing X.509 `rawData` would be insufficient: it retains the entire input even
when only its prefix was parsed. The helper declares the already-installed ASN.1
decoder directly; no package/version or unrelated resolution changes. No custom
ASN.1 parser or new crypto implementation is introduced.

Shared framing fixtures prove all nine suffix classes are rejected by both the
original Forge control and the replacement, with LF/CRLF PEM. Replacement tests
use both generated and independent OpenSSL requests, verify valid signatures,
and retain truncated/invalid ASN.1 and multiple-block refusal. All six archives
were rebuilt; only the CSR helper's implementation/manifest/provenance changes.
Its new SHA-256 is `380bb61b623eff55d94420bcd831394664aaeebf7e1e3577e8679127de54f656`;
the previous helper archive is removed from the submitted tree. The five other
archives are byte-identical. Updated hashes, SBOM and clean-source qualification
are recorded in the evidence index, not inferred from the previous receipt.

## Retained laboratory ASGI replacement and rollback

Both requirement paths, Docker default command and `legacy-lab` Compose broker
use **Uvicorn 0.54.0 / h11 0.16.0 / websockets 17.2 (sans-I/O)**. Daphne and Twisted
are absent. No proxy headers are trusted, access logs are off, ASGI lifespan is
off for unchanged Django/Channels, Compose stays loopback-only and all six legacy
profiles remain opt-in. The production Django refusal and Gateway deployment
separation are unchanged. No Compose stack was started.

The small test-only ASGI communicator avoids Channels' eager Daphne import;
actual server coverage additionally binds an ephemeral loopback socket. Both
installed requirement graphs run the whole backend suite: HTTP/CSRF, ambiguous
Content-Length refusal, unauthenticated and bad-origin WebSocket rejection,
permission checks, per-user groups, command bounds/rate limits and shutdown. The
retained service test mocks thread scheduling so it cannot launch a real fleet
or model. Database fixtures are disposable; no operator DSN is read.

Rollback is **disable the lab**, preserve the failing fixture/logs, and forward-fix
or restore a reviewed source revision only in an isolated lab. Reverting to
Daphne/Twisted reintroduces the red gate and is not an accepted release fix. Do
not loosen authentication, restore a public bind, run migrations on live data,
or use Django as a production rollback. Frontend rollback likewise cannot
silently restore vulnerable edges or disable patches; keep release admission
closed until a qualified replacement/upstream fix passes all gates.

## Independent gates and limits

The foundation workflow now executes frontend, retained Pi adapter, Gateway,
retained backend and Compose-root audits independently even if an earlier step
fails. Every failed step still fails the job; **no** continue-on-error, reduced
threshold, omitted production/dev path, renamed dependency or ignore list. It
also reproduces artifacts, emits the attributed SBOM, runs controls/consumer
qualification and checks generated protocol drift. Backend CI independently tests
both requirements graphs (`fail-fast: false`). The exact artifact-upload allowlist
adds only the Compose audit and SBOM; no broad private `.release` upload is allowed.
Other CI suites are retained.

Executed local qualification (the index pins the tested implementation hashes):

| Gate | Result |
|---|---|
| Fresh installed frontend / retained adapter `npm audit --audit-level=low --json` | Each exit 0, all severity buckets 0 |
| Gateway / retained backend / Compose installed `pip-audit==2.10.1` | Each exit 0, zero known vulnerabilities; only Gateway's editable project source is outside the package advisory database, as in the unchanged CI command |
| `npm test` | 186 passed, no failed/skipped tests, browser suites included |
| `npm run test:release` | 27 passed, including all modified consumers, whole-input CSR rejection and install/patch negative tests |
| `npm run typegen`, `npm run typecheck`, `npm run lint` | Passed; lint has 15 existing warnings, 0 errors |
| `npx expo export --platform web` | Passed, 20 static routes |
| Gateway `PYTHONPATH=. uv run pytest -v` | 636 passed, none skipped; isolated installed/verified producer fixture selected |
| Both independent lab environments, `python -m pytest -v` | 18 passed each |
| Retained adapter typecheck and `npm test` | Passed, 8 tests |
| Generated protocol drift/export and compileall | Passed; legacy contract unchanged |
| Release registry / release-foundation | Passed, 18 admission tests |
| Disposable PostgreSQL persistence/isolation/erasure | Passed; never an operator DSN |
| Deployment workflow, deploy and beta-smoke safeguards | All three fixture-based scripts passed; no live deployment |
| Reproduction / original controls / installed-byte verification / `npm ls --all` | All passed; six byte-identical artifacts, no affected graph edges or known stale implementation markers |

Development corrections are not hidden: the initial X.509 fixture needed its
declared `reflect-metadata` prerequisite, and the initial local override resolver
needed exact parent scope to retain the prior image-size bridge. Fresh installs
and all compatibility tests above qualified the corrected graph. A worktree-only
TMPDIR caused overlong Chrome/adapter sockets and correctly tripped Gateway's
outside-checkout invite-output guard; approved standard disposable test fixtures
fixed the test environment, not the production guards. Adding the two explicit
upload names required the approved exact allowlist update. The receipt tool's
overwrite refusal was preserved; reruns use new output paths.

Exact JSON reports and command results are indexed in
[`evidence/foundation-p2/summary.json`](evidence/foundation-p2/summary.json).
The final clean-source qualification and new immutable receipts bind corrected
implementation `32119f0ffd5c69395b09f8b866c167fc74679037`; all 27 command exits
are zero, with five fresh independent audit reports. The CSR review's before/after
controls are recorded in `csr-whole-input-review.json`; earlier green receipts do
not substitute for this correction's evidence.
Clean advisory results are point-in-time database evidence, not proof that code
is secure. Separate security artifact review and deployed/native acceptance are
still required. No unresolved owner policy decision is being waived here.
