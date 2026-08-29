export type AgentHistoryMessage = {
  role: 'user' | 'assistant';
  kind: 'conversation' | 'tool';
  text: string;
};

export function filterAgentHistory<T extends AgentHistoryMessage>(messages: T[], showToolCalls: boolean): T[] {
  return showToolCalls ? messages : messages.filter(message => message.kind === 'conversation');
}
