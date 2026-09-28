export interface SiriIntentTrigger {
  id: string;
  phrase: string;
  targetPath: '/voice' | '/chat' | '/attention';
  params: Record<string, string>;
}

/** Mirrors the native AppShortcutsProvider for web/config tests and diagnostics. */
export const SIRI_INTENT_REGISTRY: SiriIntentTrigger[] = [
  { id: 'start_magistrate', phrase: 'Start Magistrate', targetPath: '/voice', params: { autostart: 'true' } },
  { id: 'talk_to_magistrate', phrase: 'Talk to Magistrate', targetPath: '/voice', params: { autostart: 'true' } },
  { id: 'ask_magistrate', phrase: 'Ask Magistrate', targetPath: '/voice', params: { autostart: 'true' } },
  { id: 'whats_running', phrase: "What's running?", targetPath: '/chat', params: { shortcut: 'running' } },
  { id: 'what_needs_attention', phrase: 'What needs attention?', targetPath: '/attention', params: { overview: 'true' } },
  { id: 'magistrate_voice', phrase: 'Magistrate Voice', targetPath: '/voice', params: { autostart: 'true' } },
];

export class SiriShortcutAdapter {
  static getDeepLinkForIntent(intentId: string): string {
    const found = SIRI_INTENT_REGISTRY.find(intent => intent.id === intentId);
    const intent = found || SIRI_INTENT_REGISTRY.find(value => value.id === 'magistrate_voice')!;
    return `magistrate:${intent.targetPath}?${new URLSearchParams(intent.params).toString()}`;
  }

  static getActionButtonShortcutUrl(): string {
    return SiriShortcutAdapter.getDeepLinkForIntent('magistrate_voice');
  }
}
