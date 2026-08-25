export type InputSource = 'iphone' | 'mac' | 'ar_glasses' | 'headset' | 'watch' | 'neural_interface';
export type InputModality = 'text' | 'voice' | 'gesture' | 'gaze' | 'subvocal' | 'neural';
export type InputType = 'prompt' | 'select' | 'back' | 'scroll' | 'activate' | 'interrupt';

export interface UniversalInputPayload {
  source: InputSource;
  modality: InputModality;
  type: InputType;
  text?: string;
  target?: string;
}

export function createUniversalPayload(text: string, modality: InputModality = 'text', source: InputSource = 'iphone'): UniversalInputPayload {
  return {
    source,
    modality,
    type: 'prompt',
    text,
    target: 'captain'
  };
}
