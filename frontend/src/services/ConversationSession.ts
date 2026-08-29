import AsyncStorage from '@react-native-async-storage/async-storage';
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

// The active thread is kept in memory for reactive rendering and mirrored as
// normalized messages in AsyncStorage. Terminal scrollback is never persisted;
// Chat and Voice Mode share the 'captain' target in-session.
const messagesByTarget = new Map<string, ConversationMessage[]>();
const listenersByTarget = new Map<string, Set<() => void>>();
const EMPTY_MESSAGES: ConversationMessage[] = [];
const STORAGE_PREFIX = 'magistrate.chat.messages.';
const persist = (target: string, messages: ConversationMessage[]) => {
  void AsyncStorage.setItem(STORAGE_PREFIX + encodeURIComponent(target), JSON.stringify(messages)).catch(() => {});
};

export async function hydrateConversationMessages(target: string): Promise<ConversationMessage[]> {
  try {
    const raw = await AsyncStorage.getItem(STORAGE_PREFIX + encodeURIComponent(target));
    if (!raw) return getConversationMessages(target);
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return getConversationMessages(target);
    const normalized = parsed.filter(item => item && typeof item.id === 'string' && (item.role === 'user' || item.role === 'assistant') && typeof item.text === 'string').map(item => ({ ...item, source: item.source === 'voice' ? 'voice' : 'text' } as ConversationMessage));
    const current = messagesByTarget.get(target) || EMPTY_MESSAGES;
    const existing = new Set(normalized.map(message => `${message.role}|${message.kind || 'conversation'}|${message.text}`));
    messagesByTarget.set(target, [...normalized, ...current.filter(message => !existing.has(`${message.role}|${message.kind || 'conversation'}|${message.text}`))]);
    emit(target);
    return normalized;
  } catch { return getConversationMessages(target); }
}

function emit(target: string) {
  listenersByTarget.get(target)?.forEach(listener => listener());
}

export function appendConversationMessage(target: string, message: ConversationMessage) {
  const next = [...(messagesByTarget.get(target) || EMPTY_MESSAGES), message];
  messagesByTarget.set(target, next); persist(target, next); emit(target);
}

export function prependConversationMessages(target: string, messages: ConversationMessage[]) {
  if (!messages.length) return;
  const current = messagesByTarget.get(target) || EMPTY_MESSAGES;
  const existing = new Set(current.map(message => `${message.role}|${message.kind || 'conversation'}|${message.text}`));
  const next = [...messages.filter(message => !existing.has(`${message.role}|${message.kind || 'conversation'}|${message.text}`)), ...current];
  messagesByTarget.set(target, next); persist(target, next); emit(target);
}

export function resetConversationMessages(target: string, messages: ConversationMessage[] = EMPTY_MESSAGES) {
  messagesByTarget.set(target, messages); persist(target, messages); emit(target);
}

export function updateConversationMessage(target: string, id: string, text: string, sentAt = Date.now()) {
  const current = messagesByTarget.get(target) || EMPTY_MESSAGES;
  const next = current.map(message => message.id === id ? { ...message, text, sentAt } : message);
  messagesByTarget.set(target, next); persist(target, next); emit(target);
}

export function getConversationMessages(target: string): ConversationMessage[] {
  return messagesByTarget.get(target) || EMPTY_MESSAGES;
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
