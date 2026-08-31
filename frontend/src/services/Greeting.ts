import AsyncStorage from '@react-native-async-storage/async-storage';
import { ACCOUNT_DISPLAY_NAME_KEY } from '../api/client';

/**
 * The resting greeting on the Magi surface. The name is personalization, not a
 * claim about the account: an absent, blank, or placeholder profile name yields
 * the impersonal form rather than inventing one.
 */
const PLACEHOLDER_NAMES = new Set(['user', 'unknown', 'anonymous', 'captain', 'admin', 'n/a', 'null', 'undefined']);

export function greetingName(profileName?: string | null): string | null {
  const trimmed = (profileName || '').trim();
  if (!trimmed) return null;
  if (PLACEHOLDER_NAMES.has(trimmed.toLowerCase())) return null;
  // An email address is an identifier, not a name to greet someone by.
  if (trimmed.includes('@')) return null;
  const first = trimmed.split(/\s+/)[0];
  return first.length > 24 ? null : first;
}

export function magiGreeting(profileName?: string | null): string {
  const name = greetingName(profileName);
  return name ? `What can I help with, ${name}?` : 'What can I help with?';
}

/**
 * Reads the name the account screen last observed. Personalization must never
 * cost an authorized request of its own, so this is a local read: until a
 * profile has been seen, the greeting is simply impersonal.
 */
export async function loadMagiGreeting(): Promise<string> {
  try { return magiGreeting(await AsyncStorage.getItem(ACCOUNT_DISPLAY_NAME_KEY)); }
  catch { return magiGreeting(null); }
}
