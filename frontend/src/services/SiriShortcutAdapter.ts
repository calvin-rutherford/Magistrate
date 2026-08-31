export interface SiriIntentTrigger {
  id: string;
  phrase: string;
  targetPath: string;
  params?: Record<string, string>;
}

export const SIRI_INTENT_REGISTRY: SiriIntentTrigger[] = [
  {
    id: 'siri_use_mic',
    phrase: 'Use Magistrate mic',
    targetPath: '/voice',
    params: { autostart: 'true' }
  },
  {
    id: 'siri_talk_firstmate',
    phrase: 'Talk to Magistrate',
    targetPath: '/voice',
    params: { autostart: 'true' }
  },
  {
    id: 'siri_command_firstmate',
    phrase: 'Command Magistrate',
    targetPath: '/voice',
    params: { autostart: 'true' }
  },
  {
    id: 'siri_what_running',
    phrase: "Ask Magistrate what's running",
    targetPath: '/agents'
  },
  {
    id: 'siri_needs_attention',
    phrase: 'Ask Magistrate what needs my attention',
    targetPath: '/attention'
  }
];

export class SiriShortcutAdapter {
  static getDeepLinkForIntent(intentId: string): string {
    const found = SIRI_INTENT_REGISTRY.find(i => i.id === intentId);
    if (found) {
      const query = found.params ? '?' + new URLSearchParams(found.params).toString() : '';
      return 'magistrate:/' + found.targetPath + query;
    }
    return 'magistrate:/voice?autostart=true';
  }

  static getActionButtonShortcutUrl(): string {
    return 'magistrate:/voice?autostart=true';
  }
}
