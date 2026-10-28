# Dependency reconciliation — 2026-10-28

**Release remains BLOCKED.** This is a tested partial remediation for the
[failed main run](https://github.com/calvin-rutherford/Magistrate/actions/runs/40764813458),
not a clean security result or integration approval. Frontend advisories remain,
and the retained agent has vulnerable embedded Undici bytes that npm audit does
not report. No audit suppression, threshold reduction, omitted development graph,
framework downgrade, runtime activation or risk waiver is included.

## Source and audit evidence

Host: `melkezic-dev-01`, local execution without self-SSH. Base main:
`512bc8a9a915089b50489e9293f9e1b64def5374`. Independently audited
[PR128](https://github.com/calvin-rutherford/Magistrate/pull/128) head:
`06b17acb3e6e5f7a081dae56a5048ed0baf96fb6`. Its October 18 green checks do not
establish today's status. The unrelated runtime-admission worktree and its
uncommitted changes were inspected and preserved.

Fresh installed graphs, not lockfile-only scans, were audited independently on
main, PR128 and this candidate. npm uses `npm ci` including postinstall and
`npm audit --audit-level=low --json`; Python uses the exact frozen Gateway graph
and independently installed backend/Compose requirements with `pip-audit==2.10.1`.

| Graph | Main today | PR128 today | Candidate today |
|---|---|---|---|
| Frontend | Exit 1; 11 affected packages, 6 high/5 low | Exit 1; 10, 5 high/5 low | **Exit 1; 8, 3 high/5 low** |
| Retained adapter | Exit 1; 2 affected packages, 1 high/1 moderate | Same | Exit 0; zero scanner findings; **embedded-code blocker below** |
| Gateway | Exit 1; Certifi | Same | Exit 0; zero findings, 30 distribution records |
| Retained backend | Exit 1; Twisted | Exit 0 | Exit 0; zero findings, 48 distributions |
| Compose requirements | Exit 1; 7 affected packages | Exit 1; DRF | Exit 0; zero findings, 69 distributions |

Affected-package totals include propagated dependency entries; they are not
independent demonstrated exploits. Python advisory records can repeat aliases.
Gateway skips only this repository's editable `magistrate-gateway` source, as
before; none of its third-party dependencies is skipped. The other Python graphs
have no skipped distributions.

[Evidence summary](evidence/dependency-advisories-2026-10-28/summary.json) records
commands, exits, graph counts, exact source identities, qualification and hashes.
Adjacent `main/`, `pr128/` and `candidate/` directories contain all 15 raw reports.
Scanner results are point-in-time database evidence, not proof of security.

## Changes and compatibility

- **Forge 1.4.0 → 1.4.2**, compatible with both Expo consumers' `^1.3.3` ranges.
  The new published upstream version removes the need to adopt PR128's custom
  crypto/CSR replacement. The nested DigestAlgorithm regression reproduces
  [GHSA-86w9-cpqp-85rv](https://github.com/advisories/GHSA-86w9-cpqp-85rv)
  on the original install and rejects it after the update. Valid/wrong-digest
  signatures, CSR whole-input rejection, and actual Expo certificate/manifest/CSR
  consumers are exercised with synthetic keys and a Node crypto oracle.
- **Puppeteer 25.12.0 → 25.16.0**, within the existing `^25.12.0` range, adopts
  chromium-bidi `157.0.8090-0` and its supported Zod **4.6.7** dependency.
  Its required browser, protocol and transport dependencies change together.
  The complete browser suite and web export qualify this update. Expo 57,
  React Native 0.86.3, Metro and the existing parser compatibility patches stay.
- **External Undici 8.10.2 → 8.12.3** at both adapter resolutions, retaining the
  existing explicit postinstall replacement of the pinned agent's external
  dependency. Lock records retain real registry URLs/integrities and postinstall
  copies actual upstream bytes. This addresses the external paths for
  GHSA-4qv8-2p9j-2685, GHSA-f72v-6w45-q472, GHSA-xjj6-f33q-929c,
  GHSA-4w7w-5xrc-9h26, GHSA-2m5v-pch6-74hx, GHSA-qrxm-fqhp-jwvm,
  GHSA-9fm3-97px-rjf6 and GHSA-55wc-3pg8-jfw6. Integrity regressions fail on
  both original external paths and pass on both replacements. **This does not
  replace Undici compiled into the agent bundle.**
- **Gateway Certifi 2026.7.22 → 2026.10.23** changes only that locked
  distribution. The compromised `emSign ECC Root CA - G3` trust anchor
  (GHSA-6q48-6c7w-9gw2 / PYSEC-2026-4320) is present before and absent after.
- **Retained lab:** reuse the attributed Uvicorn 0.54.0 / h11 0.16.0 /
  websockets 17.2 migration from PR128 commit
  `3006e28a29a6950425ba21117960ec4e373640e7`, authored by
  `deployment-test <test@example.invalid>`. This removes Daphne/Twisted from both
  installed requirement graphs, rather than pretending the stable vulnerable
  Twisted version is patched. Compose takes that candidate's Django 5.2.17 LTS,
  Google adapter/protobuf/pytest/dotenv updates and advances DRF further to
  **3.18.3** for the three newly reported DRF advisories. Both graphs run the same
  compatibility suite. Lab launch definitions change with the server; loopback
  Compose publication, opt-in lab profiles, production quarantine and existing
  authorization controls remain. No Docker daemon or live lab is started.
- CI runs all five strict audits independently, including Compose, even when an
  earlier graph fails. Both backend requirement paths are tested in a matrix.
  Artifact-upload allowlists gain only the explicit Compose JSON report.

PR128's source-derived crypto/matcher packages, protocol changes and historical
evidence are not incorporated. Its branch/history remain unchanged and its
independent review requirement is not waived.

## Unresolved blockers and bounded options

1. **Deepmerge 4.3.1:**
   [GHSA-gcqx-w48m-w2h4](https://github.com/advisories/GHSA-gcqx-w48m-w2h4)
   has no published patched version. Installed `@expo/config@57.0.9` and latest
   SDK-57 `57.0.10` both require `^4.3.1`. A disposable control demonstrates that
   **both 4.3.1 and 4.1.2** poison the returned object's prototype from a malicious
   target own key. A downgrade outside the advisory range would not fix it.
   Options are a maintained upstream release or a separately reviewed consumer
   replacement/backport, covering target and source keys, null-prototype values,
   null placeholders, array cloning, symbols and Expo plugin/config semantics.
   A different merge library is not a drop-in dependency-name override.
2. **Expo CLI Zod 3.25.76:**
   [GHSA-mrhx-c7xc-m3w5](https://github.com/advisories/GHSA-mrhx-c7xc-m3w5)
   is fixed in the Zod 4 implementation from 4.6.6; the packaged `zod/v3` code
   remains affected even inside 4.6.7. CLI 57.0.27 and latest SDK-57 57.0.32 still
   require `^3.25.76`. [Zod issue 6653](https://github.com/colinhacks/zod/issues/6653)
   records the unavailable v3 release route. Expo's coordinated
   [migration proposal](https://github.com/expo/expo/pull/52287) and
   [SDK-57 backport](https://github.com/expo/expo/pull/52316) are draft/unreleased
   options, not supported published fixes. A blind Zod 4 override loses MCP
   command constraints with older tunnel converters. Owning that port requires
   a deliberate compatibility/maintenance decision, the tunnel guard and actual
   schema round-trip qualification. Switching imports to `zod/v3` would evade
   the package-version scanner without fixing the code.
3. **Embedded Undici:** the agent's `dist/bundle/chunks/chunk-OMWWHBTG.js`
   retains an executable Undici subresource-integrity module independent of its
   external `node_modules`. An isolated execution of the exact bundled factory
   accepts a wrong `SHA256-…` digest while correctly rejecting wrong `sha256-…`.
   [Control and exact hashes](evidence/dependency-advisories-2026-10-28/bundled-undici-control.json)
   prove the residual bypass; the adjacent executable control does not start
   the CLI or a session. No newer 0.84 patch release exists. Latest agent 1.4.0
   still declares external Undici 8.10.2; its bundle and changed APIs have not
   been qualified here. Options are a verified upstream rebuilt release, a
   separately reviewed reproducible source rebuild preserving the pinned agent
   contract, or a deliberate retained-adapter retirement decision. Merely
   replacing external modules or changing the lock version is insufficient.

These options require maintenance or product decisions beyond a compatible
published-version refresh. This candidate deliberately stops short of inventing
audit clearance. Independent integration review remains required before any
publication or incorporation. Merging PR128 is a separate decision.

## Validation and remaining limits

The evidence summary records the full settled results and the earlier expected
red controls/development failures. Full frontend `npm test`: **172 passed**, zero
failed/skipped. One additional Expo signing consumer case was then added and the
final release suite passed **14 tests**. Type generation/typecheck/lint and web
export pass; lint reports 15 existing warnings, zero errors, and export produces
20 routes. Adapter typecheck and **10 tests** pass. Both independently installed
lab environments pass **18 tests each**. Complete Gateway tests pass **611** with
an isolated immutable producer source fixture. Release registry validation and
**18 evidence/refusal tests** pass. No test process remains running.

The first Gateway run had 610 passes and one SPA failure because its frontend
export prerequisite had not finished; the correctly ordered full rerun passes.
The first export's completion was logged but its exit receipt was unavailable
after a task checkpoint; the final explicit export has a recorded zero exit.
No producer activation, dispatch, deployment, restart or real provider call is
performed.

Linux tests do not certify native/Xcode/device behavior, live Watchman/keychain,
hosted isolation, production readiness or independent security approval. Hosted
CI has not been run because this branch remains local pending independent review.
Frontend CI is expected to remain red under the unchanged low audit threshold.
