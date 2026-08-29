import AsyncStorage from '@react-native-async-storage/async-storage';
import { useCallback, useEffect, useState, useSyncExternalStore } from 'react';

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
const hydratedTargets = new Set<string>();
const hydrationPromises = new Map<string, Promise<void>>();
const EMPTY_MESSAGES: ConversationMessage[] = [];

// Chat history must survive a full app reload, not just a remount within the
// same JS session, so every mutation is mirrored to AsyncStorage keyed by
// target. Hydration is lazy and async: callers await hydrateConversationMessages
// before trusting an empty in-memory list to mean "no history".
const storageKey = (target: string) => `magistrate.chat.thread.${target}`;

function emit(target: string) {
  listenersByTarget.get(target)?.forEach(listener => listener());
}

function persist(target: string) {
  const messages = messagesByTarget.get(target) || EMPTY_MESSAGES;
  AsyncStorage.setItem(storageKey(target), JSON.stringify(messages)).catch(() => { /* Best-effort: an unpersisted turn is not worth surfacing an error for. */ });
}

export function appendConversationMessage(target: string, message: ConversationMessage) {
  messagesByTarget.set(target, [...(messagesByTarget.get(target) || EMPTY_MESSAGES), message]);
  emit(target);
  persist(target);
}

export function resetConversationMessages(target: string, messages: ConversationMessage[] = EMPTY_MESSAGES) {
  messagesByTarget.set(target, messages);
  emit(target);
  persist(target);
}

export function updateConversationMessage(target: string, id: string, text: string, sentAt = Date.now()) {
  const current = messagesByTarget.get(target) || EMPTY_MESSAGES;
  messagesByTarget.set(target, current.map(message => message.id === id ? { ...message, text, sentAt } : message));
  emit(target);
  persist(target);
}

// Explicit "New Session" action only: automatic flows must never call this,
// or a captain thread would silently lose history the captain expects back.
export function clearConversationMessages(target: string) {
  hydratedTargets.add(target);
  hydrationPromises.delete(target);
  resetConversationMessages(target, []);
}

export function getConversationMessages(target: string): ConversationMessage[] {
  return messagesByTarget.get(target) || EMPTY_MESSAGES;
}

// Loads the persisted thread for `target` into memory once per target/app
// lifetime, unless something (e.g. a live remount) already populated it.
export function hydrateConversationMessages(target: string): Promise<void> {
  if (hydratedTargets.has(target)) return hydrationPromises.get(target) || Promise.resolve();
  hydratedTargets.add(target);
  const promise = AsyncStorage.getItem(storageKey(target)).then(raw => {
    if (!raw || messagesByTarget.has(target)) return;
    try {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed) && parsed.length) { messagesByTarget.set(target, parsed); emit(target); }
    } catch { /* Corrupt persisted thread: fall back to starting empty. */ }
  }).catch(() => { /* Storage unavailable: fall back to starting empty. */ });
  hydrationPromises.set(target, promise);
  return promise;
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

// Convenience for callers that must not render before the persisted thread
// (if any) has had a chance to load, e.g. to avoid a flash of empty history.
export function useConversationHydration(target: string): boolean {
  const [hydrated, setHydrated] = useState(hydratedTargets.has(target) && !hydrationPromises.get(target));
  useEffect(() => {
    let mounted = true;
    setHydrated(false);
    hydrateConversationMessages(target).finally(() => { if (mounted) setHydrated(true); });
    return () => { mounted = false; };
  }, [target]);
  return hydrated;
}
