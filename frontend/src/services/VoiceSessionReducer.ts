export type VoiceState = 'READY' | 'LISTENING' | 'TRANSCRIBING' | 'REVIEW' | 'RESOLVING' |
  'CONFIRMING' | 'EXECUTING' | 'SPEAKING' | 'ERROR';

const allowed: Record<VoiceState, VoiceState[]> = {
  READY: ['LISTENING', 'ERROR'],
  LISTENING: ['TRANSCRIBING', 'READY', 'ERROR'],
  TRANSCRIBING: ['REVIEW', 'READY', 'ERROR'],
  REVIEW: ['RESOLVING', 'LISTENING', 'READY', 'ERROR'],
  RESOLVING: ['CONFIRMING', 'EXECUTING', 'ERROR', 'READY'],
  CONFIRMING: ['EXECUTING', 'READY', 'ERROR'],
  EXECUTING: ['SPEAKING', 'READY', 'ERROR'],
  SPEAKING: ['READY', 'LISTENING', 'ERROR'],
  ERROR: ['READY', 'LISTENING'],
};

export function transitionVoiceState(current: VoiceState, next: VoiceState): VoiceState {
  if (current === next || allowed[current].includes(next)) return next;
  throw new Error(`Invalid Voice Mode transition: ${current} -> ${next}`);
}
