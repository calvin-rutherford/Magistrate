/**
 * Speech selection is deliberately independent from the execution harness.
 * A transcript may be sent to any configured Firstmate/Herdr target after it
 * has been produced by one of these input modes.
 */
export type VoiceInputMode = 'automatic' | 'browser' | 'native' | 'openai';
export type VoiceCapabilityState = 'available' | 'unavailable' | 'unknown';

export interface VoiceInputCapability {
  id: VoiceInputMode;
  label: string;
  available: VoiceCapabilityState;
  reason?: string;
}

export interface VoiceInputCapabilities {
  modes: VoiceInputCapability[];
  selected?: VoiceInputMode;
  serverProvider?: string;
  serverConfigured?: boolean;
}

export const VOICE_INPUT_MODE_KEY = 'magistrate.voice.input-mode';
export const DEFAULT_VOICE_INPUT_MODE: VoiceInputMode = 'automatic';

export const VOICE_INPUT_MODE_OPTIONS: { id: VoiceInputMode; label: string; description: string }[] = [
  { id: 'automatic', label: 'Automatic', description: 'Use the best available speech path' },
  { id: 'browser', label: 'Browser speech', description: 'On-device browser speech recognition' },
  { id: 'native', label: 'Native device', description: 'Device microphone with gateway transcription' },
  { id: 'openai', label: 'Gateway OpenAI', description: 'Gateway transcription; credentials stay server-side' },
];

export function browserSpeechRecognitionAvailable(): boolean {
  if (typeof window === 'undefined') return false;
  const value = window as typeof window & { SpeechRecognition?: unknown; webkitSpeechRecognition?: unknown };
  return typeof value.SpeechRecognition === 'function' || typeof value.webkitSpeechRecognition === 'function';
}

export function getLocalVoiceCapabilities(serverConfigured?: boolean): VoiceInputCapabilities {
  const browserAvailable = browserSpeechRecognitionAvailable();
  const nativeAvailable = typeof window === 'undefined';
  const gatewayState: VoiceCapabilityState = typeof serverConfigured === 'boolean'
    ? serverConfigured ? 'available' : 'unavailable'
    : 'unknown';
  return {
    modes: [
      { id: 'automatic', label: 'Automatic', available: browserAvailable || nativeAvailable || gatewayState === 'available' ? 'available' : gatewayState, reason: 'Chooses a compatible speech path for this device.' },
      { id: 'browser', label: 'Browser speech', available: browserAvailable ? 'available' : 'unavailable', reason: browserAvailable ? undefined : 'This browser does not expose SpeechRecognition.' },
      { id: 'native', label: 'Native device', available: nativeAvailable ? 'available' : 'unavailable', reason: nativeAvailable ? undefined : 'Native microphone capture is only available in the iOS/Android build.' },
      { id: 'openai', label: 'Gateway OpenAI', available: gatewayState, reason: gatewayState === 'unavailable' ? 'The authenticated gateway has no speech provider configured.' : gatewayState === 'unknown' ? 'Gateway speech capability is still being checked.' : undefined },
    ],
  };
}

export function capabilityFor(capabilities: VoiceInputCapabilities, mode: VoiceInputMode): VoiceInputCapability {
  return capabilities.modes.find(item => item.id === mode) || {
    id: mode,
    label: mode,
    available: 'unavailable',
    reason: 'This speech mode is not supported by the current app build.',
  };
}

/** Return the selected mode and whether it had to fall back to Automatic. */
export function resolveVoiceInputMode(selected: VoiceInputMode, capabilities: VoiceInputCapabilities): { mode: VoiceInputMode; fallbackReason?: string } {
  const chosen = capabilityFor(capabilities, selected);
  // Unknown means an older/temporarily unreachable gateway, not a claim that
  // the provider is supported. Keep the user's choice and let the operation
  // surface a truthful authenticated error instead of silently switching.
  if (chosen.available !== 'unavailable') return { mode: selected };
  const automatic = capabilityFor(capabilities, 'automatic');
  if (automatic.available === 'available' || automatic.available === 'unknown') {
    return { mode: 'automatic', fallbackReason: `${chosen.label} is unavailable. Using Automatic instead.` };
  }
  return { mode: selected, fallbackReason: chosen.reason || `${chosen.label} is unavailable.` };
}

/** Browser mode is the only mode whose final transcript is local. */
export function usesBrowserSpeech(mode: VoiceInputMode): boolean {
  return mode === 'browser' || (mode === 'automatic' && browserSpeechRecognitionAvailable());
}
