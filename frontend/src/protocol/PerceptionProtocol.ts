/** Device/provider-neutral perception protocol v1.
 *
 * Adapters emit observations only. They do not mint principal identity and
 * they never dispatch an action from inferred intent.
 */
export const PERCEPTION_SCHEMA_VERSION = 'magistrate.perception-event.v1' as const;
export const PERCEPTION_LOW_CONFIDENCE = 0.75;

export type PerceptionModality = 'text' | 'voice' | 'image' | 'gesture' | 'ambient' | 'spatial' | 'subvocal' | 'neural';
export type DeviceClass = 'phone' | 'web' | 'desktop' | 'headset' | 'wearable' | 'gaming' | 'assistive' | 'unknown';
export type AdapterTransform = 'none' | 'speech-to-text' | 'gesture-map' | 'classifier' | 'neural-decoder';

export interface PerceptionEventV1 {
  schema_version: typeof PERCEPTION_SCHEMA_VERSION;
  event_id: string;
  client: { client_id: string; device_class: DeviceClass; adapter_id: string; adapter_version: string };
  modality: PerceptionModality;
  observed_at_ms: number;
  context: { project_id?: string; conversation_id?: string; surface?: string };
  confidence: number;
  consent: {
    captured: true;
    purpose: 'conversation' | 'accessibility' | 'command-draft' | 'context';
    retention_seconds: number;
    biometric_processing: boolean;
  };
  artifact_ref?: string;
  intent: {
    kind: string;
    impact: 'none' | 'low' | 'high';
    provenance: { transcript?: string; adapter_transform: AdapterTransform };
  };
}

export type PerceptionResultV1 = Omit<PerceptionEventV1, 'schema_version' | 'artifact_ref'> & {
  schema_version: 'magistrate.perception-result.v1';
  artifact_ref: string | null;
  principal: { id: string };
  authorization: {
    state: 'draft' | 'confirmation-required' | 'confirmed';
    reason: string | null;
    revision: number;
    executes_action: false;
  };
  retention: { expires_at: number };
  duplicate?: true;
};

/** Local fail-safe for renderers. Gateway validation remains authoritative. */
export function perceptionRequiresConfirmation(event: PerceptionEventV1): boolean {
  return event.confidence < PERCEPTION_LOW_CONFIDENCE
    || event.intent.impact === 'high'
    || event.modality === 'neural'
    || event.modality === 'subvocal';
}

const exactKeys = (value: Record<string, unknown>, required: string[], optional: string[] = []) => {
  const keys = Object.keys(value);
  return required.every(key => keys.includes(key))
    && keys.every(key => required.includes(key) || optional.includes(key));
};

export function validatePerceptionEventV1(value: unknown): value is PerceptionEventV1 {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const event = value as Record<string, any>;
  const modalities = new Set<PerceptionModality>(['text', 'voice', 'image', 'gesture', 'ambient', 'spatial', 'subvocal', 'neural']);
  const deviceClasses = new Set<DeviceClass>(['phone', 'web', 'desktop', 'headset', 'wearable', 'gaming', 'assistive', 'unknown']);
  const artifactRequired = ['image', 'ambient', 'spatial'].includes(event.modality);
  const client = event.client as Record<string, unknown> | undefined;
  const context = event.context as Record<string, unknown> | undefined;
  const consent = event.consent as Record<string, unknown> | undefined;
  const intent = event.intent as Record<string, any> | undefined;
  const provenance = intent?.provenance as Record<string, unknown> | undefined;
  return exactKeys(event, ['schema_version', 'event_id', 'client', 'modality', 'observed_at_ms', 'context', 'confidence', 'consent', 'intent'], ['artifact_ref'])
    && event.schema_version === PERCEPTION_SCHEMA_VERSION
    && typeof event.event_id === 'string' && /^pev_[A-Za-z0-9_-]{12,96}$/.test(event.event_id)
    && !!client && exactKeys(client, ['client_id', 'device_class', 'adapter_id', 'adapter_version'])
    && typeof client.client_id === 'string' && /^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$/.test(client.client_id)
    && deviceClasses.has(client.device_class as DeviceClass)
    && typeof client.adapter_id === 'string' && /^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/.test(client.adapter_id)
    && typeof client.adapter_version === 'string' && /^[0-9]+(?:\.[0-9]+){0,3}$/.test(client.adapter_version)
    && modalities.has(event.modality)
    && Number.isSafeInteger(event.observed_at_ms) && event.observed_at_ms >= 0
    && !!context && exactKeys(context, [], ['project_id', 'conversation_id', 'surface'])
    && typeof event.confidence === 'number' && Number.isFinite(event.confidence)
    && event.confidence >= 0 && event.confidence <= 1
    && !!consent && exactKeys(consent, ['captured', 'purpose', 'retention_seconds', 'biometric_processing'])
    && consent.captured === true
    && ['conversation', 'accessibility', 'command-draft', 'context'].includes(consent.purpose as string)
    && Number.isSafeInteger(consent.retention_seconds)
    && Number(consent.retention_seconds) >= 300 && Number(consent.retention_seconds) <= 30 * 24 * 60 * 60
    && (!['neural', 'subvocal'].includes(event.modality) || consent.biometric_processing === true)
    && (event.artifact_ref === undefined || (typeof event.artifact_ref === 'string'
      && /^[A-Za-z0-9_-]{16,64}$/.test(event.artifact_ref)))
    && (!artifactRequired || typeof event.artifact_ref === 'string')
    && !!intent && exactKeys(intent, ['kind', 'impact', 'provenance'])
    && ['none', 'low', 'high'].includes(intent.impact)
    && typeof intent.kind === 'string' && /^[a-z][a-z0-9.-]*$/.test(intent.kind)
    && !!provenance && exactKeys(provenance, ['adapter_transform'], ['transcript'])
    && ['none', 'speech-to-text', 'gesture-map', 'classifier', 'neural-decoder'].includes(provenance.adapter_transform as string)
    && (provenance.adapter_transform !== 'neural-decoder' || event.modality === 'neural');
}

export function validatePerceptionResultV1(value: unknown): value is PerceptionResultV1 {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const result = value as Record<string, any>;
  const eventCandidate: Record<string, unknown> = { ...result, schema_version: PERCEPTION_SCHEMA_VERSION };
  delete eventCandidate.principal;
  delete eventCandidate.authorization;
  delete eventCandidate.retention;
  delete eventCandidate.duplicate;
  if (eventCandidate.artifact_ref === null) delete eventCandidate.artifact_ref;
  const authorization = result.authorization as Record<string, unknown> | undefined;
  const retention = result.retention as Record<string, unknown> | undefined;
  return exactKeys(result, [
    'schema_version', 'event_id', 'principal', 'client', 'modality', 'observed_at_ms',
    'context', 'confidence', 'consent', 'artifact_ref', 'intent', 'authorization', 'retention',
  ], ['duplicate'])
    && result.schema_version === 'magistrate.perception-result.v1'
    && result.principal && exactKeys(result.principal, ['id']) && typeof result.principal.id === 'string'
    && validatePerceptionEventV1(eventCandidate)
    && !!authorization && exactKeys(authorization, ['state', 'reason', 'revision', 'executes_action'])
    && ['draft', 'confirmation-required', 'confirmed'].includes(authorization.state as string)
    && (authorization.reason === null || typeof authorization.reason === 'string')
    && Number.isSafeInteger(authorization.revision) && Number(authorization.revision) >= 1
    && authorization.executes_action === false
    && !!retention && exactKeys(retention, ['expires_at'])
    && Number.isSafeInteger(retention.expires_at) && Number(retention.expires_at) > 0
    && (result.duplicate === undefined || result.duplicate === true);
}
