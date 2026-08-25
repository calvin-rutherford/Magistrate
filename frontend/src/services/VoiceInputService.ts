export interface NormalizedInputContract {
  source: 'iphone' | 'siri' | 'action_button' | 'ar_glasses' | 'headset' | 'keyboard';
  modality: 'voice' | 'text' | 'gesture';
  text: string;
  target?: string;
  timestamp?: number;
}

export class VoiceInputService {
  static normalizeInput(text: string, source: NormalizedInputContract['source'] = 'iphone'): NormalizedInputContract {
    return {
      source,
      modality: 'voice',
      text: text.trim(),
      target: 'captain',
      timestamp: Date.now()
    };
  }
}
