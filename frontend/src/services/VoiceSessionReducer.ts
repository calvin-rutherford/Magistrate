export type VoiceState = 'READY' | 'STARTING' | 'LISTENING' | 'TRANSCRIBING' | 'THINKING' |
  'CONFIRMING' | 'SPEAKING' | 'ERROR';

const allowed: Record<VoiceState, VoiceState[]> = {
  READY: ['STARTING', 'ERROR'],
  STARTING: ['LISTENING', 'ERROR', 'READY'],
  LISTENING: ['TRANSCRIBING', 'READY', 'ERROR'],
  TRANSCRIBING: ['THINKING', 'STARTING', 'LISTENING', 'ERROR', 'READY'],
  THINKING: ['CONFIRMING', 'SPEAKING', 'STARTING', 'LISTENING', 'ERROR', 'READY'],
  CONFIRMING: ['THINKING', 'STARTING', 'LISTENING', 'READY', 'ERROR'],
  SPEAKING: ['STARTING', 'LISTENING', 'READY', 'ERROR'],
  ERROR: ['STARTING', 'LISTENING', 'READY'],
};

export function transitionVoiceState(current: VoiceState, next: VoiceState): VoiceState {
  if (current === next || allowed[current].includes(next)) return next;
  throw new Error(`Invalid Voice Mode transition: ${current} -> ${next}`);
}
