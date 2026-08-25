export type VoiceState = 'IDLE' | 'LISTENING' | 'TRANSCRIBING' | 'THINKING' | 'SPEAKING';

export interface VoiceStateListener {
  (state: VoiceState, detail?: string): void;
}

export class VoiceSessionStateMachine {
  private currentState: VoiceState = 'IDLE';
  private listeners: VoiceStateListener[] = [];

  getState(): VoiceState {
    return this.currentState;
  }

  setState(newState: VoiceState, detail?: string) {
    this.currentState = newState;
    this.listeners.forEach(l => l(newState, detail));
  }

  subscribe(listener: VoiceStateListener): () => void {
    this.listeners.push(listener);
    listener(this.currentState);
    return () => {
      this.listeners = this.listeners.filter(l => l !== listener);
    };
  }
}

export const voiceStateMachine = new VoiceSessionStateMachine();
