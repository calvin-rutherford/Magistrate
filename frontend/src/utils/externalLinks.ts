import * as Linking from 'expo-linking';
import { Platform } from 'react-native';

export type OpenExternalResult = { ok: true } | { ok: false; message: string };

export function validatedWebUrl(value?: string | null): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.toString() : null;
  } catch {
    return null;
  }
}

export async function openExternalUrl(value?: string | null): Promise<OpenExternalResult> {
  const url = validatedWebUrl(value);
  if (!url) return { ok: false, message: 'This link is not a valid web address.' };
  try {
    if (Platform.OS === 'web') {
      if (typeof window === 'undefined') return { ok: false, message: 'A browser window is not available.' };
      const opened = window.open(url, '_blank', 'noopener,noreferrer');
      if (!opened) return { ok: false, message: 'Your browser blocked the new tab. Allow pop-ups and try again.' };
      opened.opener = null;
      return { ok: true };
    }
    if (!(await Linking.canOpenURL(url))) return { ok: false, message: 'No browser is available to open this link.' };
    await Linking.openURL(url);
    return { ok: true };
  } catch {
    return { ok: false, message: 'GitHub could not be opened. Please try again.' };
  }
}
