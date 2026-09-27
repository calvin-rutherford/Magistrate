import assert from 'node:assert/strict';
import test from 'node:test';
import {
  perceptionRequiresConfirmation, validatePerceptionEventV1, validatePerceptionResultV1,
  type PerceptionEventV1,
} from '../src/protocol/PerceptionProtocol';

const base = (): PerceptionEventV1 => ({
  schema_version: 'magistrate.perception-event.v1',
  event_id: 'pev_clientprotocol_0001',
  client: {
    client_id: 'client-device-0001', device_class: 'wearable',
    adapter_id: 'vendor.gesture', adapter_version: '1.0',
  },
  modality: 'gesture', observed_at_ms: Date.now(),
  context: { project_id: 'Magistrate', surface: 'attention' }, confidence: 0.9,
  consent: {
    captured: true, purpose: 'command-draft', retention_seconds: 600,
    biometric_processing: false,
  },
  intent: {
    kind: 'attention.open', impact: 'low',
    provenance: { adapter_transform: 'gesture-map' },
  },
});

test('v1 remains device-neutral and rejects incompatible schema versions', () => {
  for (const device_class of ['phone', 'web', 'desktop', 'headset', 'wearable', 'gaming', 'assistive', 'unknown'] as const) {
    assert.equal(validatePerceptionEventV1({ ...base(), client: { ...base().client, device_class } }), true);
  }
  assert.equal(validatePerceptionEventV1({ ...base(), schema_version: 'magistrate.perception-event.v2' }), false);
  assert.equal(validatePerceptionEventV1({ ...base(), principal: { id: 'forged' } }), false);
});

test('result validation requires server principal and a non-executing authorization', () => {
  const input = base();
  const result = {
    ...input, schema_version: 'magistrate.perception-result.v1', artifact_ref: null,
    principal: { id: 'owner' },
    authorization: { state: 'draft', reason: null, revision: 1, executes_action: false },
    retention: { expires_at: Math.floor(Date.now() / 1000) + 600 },
  };
  assert.equal(validatePerceptionResultV1(result), true);
  assert.equal(validatePerceptionResultV1({
    ...result, authorization: { ...result.authorization, executes_action: true },
  }), false);
});

test('local renderer fails safe for low-confidence, high-impact, and neural intent', () => {
  assert.equal(perceptionRequiresConfirmation(base()), false);
  assert.equal(perceptionRequiresConfirmation({ ...base(), confidence: 0.2 }), true);
  assert.equal(perceptionRequiresConfirmation({ ...base(), intent: { ...base().intent, impact: 'high' } }), true);
  assert.equal(perceptionRequiresConfirmation({
    ...base(), modality: 'neural',
    consent: { ...base().consent, biometric_processing: true },
    intent: { ...base().intent, provenance: { adapter_transform: 'neural-decoder' } },
  }), true);
});

test('byte-bearing and biometric modalities require references and consent', () => {
  assert.equal(validatePerceptionEventV1({ ...base(), modality: 'image' }), false);
  assert.equal(validatePerceptionEventV1({ ...base(), modality: 'image', artifact_ref: 'upload_1234567890' }), true);
  assert.equal(validatePerceptionEventV1({ ...base(), modality: 'neural' }), false);
});
