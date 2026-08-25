export type GestureAction = 'select' | 'activate' | 'back' | 'scroll_up' | 'scroll_down' | 'next' | 'previous' | 'open_chat' | 'open_attention';

export interface GestureEvent {
  action: GestureAction;
  target_id?: string;
}

export class GestureInputAdapter {
  handleGesture(action: GestureAction, target_id?: string): GestureEvent {
    return { action, target_id };
  }
}

export const gestureInputAdapter = new GestureInputAdapter();
