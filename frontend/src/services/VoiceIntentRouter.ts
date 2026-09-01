export interface ClassifiedIntent {
  intent: 'attention_summary' | 'agent_status' | 'jira_summary' | 'teams_summary' | 'github_pr' | 'firstmate_command';
  targetPath: string;
  requiresHighImpactConfirmation: boolean;
  confirmationMessage?: string;
  payloadText: string;
}

export class VoiceIntentRouter {
  static classify(text: string): ClassifiedIntent {
    const lower = text.toLowerCase();

    // High Impact Safety Check
    if (lower.includes('deploy') || lower.includes('merge pr') || lower.includes('delete branch')) {
      return {
        intent: 'firstmate_command',
        targetPath: '/chat',
        requiresHighImpactConfirmation: true,
        confirmationMessage: `HIGH IMPACT ACTION: Magistrate requests confirmation to "${text}". Confirm?`,
        payloadText: text
      };
    }

    if (lower.includes('needs me') || lower.includes('needs my attention') || lower.includes('what needs attention')) {
      return {
        intent: 'attention_summary',
        targetPath: '/attention',
        requiresHighImpactConfirmation: false,
        payloadText: text
      };
    }

    if (lower.includes('jira')) {
      return {
        intent: 'jira_summary',
        targetPath: '/attention',
        requiresHighImpactConfirmation: false,
        payloadText: text
      };
    }

    if (lower.includes('teams')) {
      return {
        intent: 'teams_summary',
        targetPath: '/attention',
        requiresHighImpactConfirmation: false,
        payloadText: text
      };
    }

    if (lower.includes('agent') || lower.includes('running') || lower.includes('what is working')) {
      return {
        intent: 'agent_status',
        targetPath: '/agents',
        requiresHighImpactConfirmation: false,
        payloadText: text
      };
    }

    if (lower.includes('pr') || lower.includes('pull request')) {
      return {
        intent: 'github_pr',
        targetPath: '/prs',
        requiresHighImpactConfirmation: false,
        payloadText: text
      };
    }

    return {
      intent: 'firstmate_command',
      targetPath: '/chat',
      requiresHighImpactConfirmation: false,
      payloadText: text
    };
  }
}
