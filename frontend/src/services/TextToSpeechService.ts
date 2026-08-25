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

  setSettings(newSettings: Partial<TTSSettings>) {
    this.settings = { ...this.settings, ...newSettings };
  }

  getSettings(): TTSSettings {
    return this.settings;
  }

  async speakChunk(text: string, onDone?: () => void): Promise<void> {
    if (!this.settings.enabled || !text.trim()) {
      if (onDone) onDone();
      return;
    }

    try {
      this.isSpeakingFlag = true;
      Speech.speak(text, {
        rate: this.settings.rate,
        pitch: this.settings.pitch,
        voice: this.settings.voice,
        onDone: () => {
          this.isSpeakingFlag = false;
          if (onDone) onDone();
        },
        onError: () => {
          this.isSpeakingFlag = false;
          if (onDone) onDone();
        }
      });
    } catch (e) {
      console.error('TTS Speak error:', e);
      this.isSpeakingFlag = false;
      if (onDone) onDone();
    }
  }

  stop(): void {
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
