import { useCallback, useSyncExternalStore } from 'react';

export interface ConversationMessage {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  // Agent history replayed from Herdr terminal snapshots has no reliable
  // wall-clock time, so timestamps stay optional for those messages.
  sentAt?: number;
  source: 'text' | 'voice';
  kind?: 'conversation' | 'tool';
}

const messagesByTarget = new Map<string, ConversationMessage[]>();
const listenersByTarget = new Map<string, Set<() => void>>();
const EMPTY_MESSAGES: ConversationMessage[] = [];

function emit(target: string) {
  listenersByTarget.get(target)?.forEach(listener => listener());
}

export function appendConversationMessage(target: string, message: ConversationMessage) {
  messagesByTarget.set(target, [...(messagesByTarget.get(target) || EMPTY_MESSAGES), message]);
  emit(target);
}

export function resetConversationMessages(target: string, messages: ConversationMessage[] = EMPTY_MESSAGES) {
  messagesByTarget.set(target, messages);
  emit(target);
}

export function updateConversationMessage(target: string, id: string, text: string, sentAt = Date.now()) {
  const current = messagesByTarget.get(target) || EMPTY_MESSAGES;
  messagesByTarget.set(target, current.map(message => message.id === id ? { ...message, text, sentAt } : message));
  emit(target);
}

export function useConversationMessages(target: string): ConversationMessage[] {
  const subscribe = useCallback((listener: () => void) => {
    const listeners = listenersByTarget.get(target) || new Set<() => void>();
    listeners.add(listener);
    listenersByTarget.set(target, listeners);
    return () => {
      listeners.delete(listener);
      if (listeners.size === 0) listenersByTarget.delete(target);
    };
  }, [target]);
  const getSnapshot = useCallback(() => messagesByTarget.get(target) || EMPTY_MESSAGES, [target]);
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}
