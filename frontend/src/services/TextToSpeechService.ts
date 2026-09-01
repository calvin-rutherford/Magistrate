import * as Speech from 'expo-speech';

export interface TTSSettings {
  enabled: boolean;
  autoSpeak: boolean;
  voice?: string;
  rate: number;
  pitch: number;
}

export class TextToSpeechService {
  private settings: TTSSettings = {
    enabled: true,
    autoSpeak: true,
    rate: 1.0,
    pitch: 1.0
  };

  private isSpeakingFlag: boolean = false;
  private lastSpokenText: string = '';
  // Incremented by stop() and each new utterance so completion callbacks from a
  // cancelled utterance (browsers fire end/error on cancel) can never fire twice
  // or after a barge-in.
  private speakGeneration: number = 0;

  setSettings(newSettings: Partial<TTSSettings>) {
    this.settings = { ...this.settings, ...newSettings };
  }

  getSettings(): TTSSettings {
    return this.settings;
  }

  speakNewResponse(fullText: string, onDone?: () => void): void {
    if (!this.settings.enabled || !fullText.trim()) {
      if (onDone) onDone();
      return;
    }

    // Extract only newly added content to prevent screen-reader behavior
    let newChunk = fullText;
    if (this.lastSpokenText && fullText.startsWith(this.lastSpokenText)) {
      newChunk = fullText.slice(this.lastSpokenText.length);
    } else if (this.lastSpokenText && fullText.includes(this.lastSpokenText)) {
      newChunk = fullText.split(this.lastSpokenText).pop() || '';
    }

    newChunk = newChunk.replace(/^\[.*?\]/, '').trim();
    if (!newChunk) {
      if (onDone) onDone();
      return;
    }

    this.lastSpokenText = fullText;
    this.speakChunk(newChunk, onDone);
  }

  speakChunk(text: string, onDone?: () => void): void {
    if (!this.settings.enabled || !text.trim()) {
      if (onDone) onDone();
      return;
    }

    const generation = ++this.speakGeneration;
    const finish = () => {
      if (generation !== this.speakGeneration) return;
      this.isSpeakingFlag = false;
      if (onDone) onDone();
    };
    try {
      this.isSpeakingFlag = true;
      Speech.speak(text, {
        rate: this.settings.rate,
        pitch: this.settings.pitch,
        voice: this.settings.voice,
        onDone: finish,
        onError: finish
      });
    } catch (e) {
      console.error('TTS Speak error:', e);
      finish();
    }
  }

  stop(): void {
    this.speakGeneration++;
    try {
      Speech.stop();
      this.isSpeakingFlag = false;
    } catch (e) {
      console.error('TTS Stop error:', e);
    }
  }

  isSpeaking(): boolean {
    return this.isSpeakingFlag;
  }
}

export const ttsService = new TextToSpeechService();
