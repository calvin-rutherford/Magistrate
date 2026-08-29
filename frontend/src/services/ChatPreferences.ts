import AsyncStorage from '@react-native-async-storage/async-storage';
import { useEffect, useState } from 'react';
import { Appearance, useColorScheme } from 'react-native';
import { setActiveBackground, WeatherSceneKey } from './environmentTheme';

export const TOOL_CALL_VISIBILITY_KEY = 'magistrate.chat.show-tool-calls';
export const CHAT_THEME_MODE_KEY = 'magistrate.chat.theme-mode';
export const CHAT_BACKGROUND_KEY = 'magistrate.chat.background';
export const CHAT_CUSTOM_BACKGROUND_KEY = 'magistrate.chat.custom-background';

export type ChatThemeMode = 'system' | 'dark' | 'light';

export type ChatPreferences = {
  showToolCalls: boolean;
  themeMode: ChatThemeMode;
  background: WeatherSceneKey;
  customBackgroundUri?: string;
};

export const DEFAULT_CHAT_PREFERENCES: ChatPreferences = {
  showToolCalls: false,
  themeMode: 'system',
  background: 'auto',
  customBackgroundUri: undefined,
};

const validThemeModes = new Set<ChatThemeMode>(['system', 'dark', 'light']);
const validBackgrounds = new Set<WeatherSceneKey>([
  'auto', 'dusk-mountain', 'clear-day', 'clear-night', 'clouds', 'rain', 'storm', 'sunset', 'minimal-dark', 'custom',
]);

export async function loadChatPreferences(): Promise<ChatPreferences> {
  const [toolCalls, themeMode, background, customBackground] = await AsyncStorage.multiGet([
    TOOL_CALL_VISIBILITY_KEY,
    CHAT_THEME_MODE_KEY,
    CHAT_BACKGROUND_KEY,
    CHAT_CUSTOM_BACKGROUND_KEY,
  ]);
  const preferences: ChatPreferences = {
    showToolCalls: toolCalls[1] === 'true',
    themeMode: validThemeModes.has(themeMode[1] as ChatThemeMode) ? themeMode[1] as ChatThemeMode : DEFAULT_CHAT_PREFERENCES.themeMode,
    background: validBackgrounds.has(background[1] as WeatherSceneKey) ? background[1] as WeatherSceneKey : DEFAULT_CHAT_PREFERENCES.background,
    customBackgroundUri: customBackground[1] || undefined,
  };
  applyChatAppearance(preferences);
  return preferences;
}

// react-native-web's Appearance.setColorScheme does not drive useColorScheme,
// so the selected mode lives in a subscribable module store as well.
let activeThemeMode: ChatThemeMode = 'system';
const themeModeListeners = new Set<(mode: ChatThemeMode) => void>();

function setThemeModeOverride(themeMode: ChatThemeMode): void {
  activeThemeMode = themeMode;
  themeModeListeners.forEach(listener => listener(themeMode));
  if (typeof Appearance.setColorScheme === 'function') Appearance.setColorScheme(themeMode === 'system' ? 'unspecified' : themeMode);
}

export function useChatColorScheme(): 'light' | 'dark' {
  const system = useColorScheme();
  const [mode, setMode] = useState(activeThemeMode);
  useEffect(() => {
    themeModeListeners.add(setMode);
    return () => { themeModeListeners.delete(setMode); };
  }, []);
  return mode === 'system' ? (system === 'light' ? 'light' : 'dark') : mode;
}

export function applyChatAppearance(preferences: Pick<ChatPreferences, 'themeMode' | 'background' | 'customBackgroundUri'>): void {
  setThemeModeOverride(preferences.themeMode);
  setActiveBackground(preferences.background, preferences.customBackgroundUri);
}

export async function saveToolCallVisibility(showToolCalls: boolean): Promise<void> {
  await AsyncStorage.setItem(TOOL_CALL_VISIBILITY_KEY, String(showToolCalls));
}

export async function saveThemeMode(themeMode: ChatThemeMode): Promise<void> {
  setThemeModeOverride(themeMode);
  await AsyncStorage.setItem(CHAT_THEME_MODE_KEY, themeMode);
}

export async function saveChatBackground(background: WeatherSceneKey): Promise<void> {
  setActiveBackground(background);
  await AsyncStorage.setItem(CHAT_BACKGROUND_KEY, background);
}

export async function saveCustomBackground(uri: string): Promise<void> {
  setActiveBackground('custom', uri);
  await AsyncStorage.multiSet([[CHAT_BACKGROUND_KEY, 'custom'], [CHAT_CUSTOM_BACKGROUND_KEY, uri]]);
}

export async function removeCustomBackground(): Promise<void> {
  setActiveBackground('auto');
  await AsyncStorage.multiRemove([CHAT_BACKGROUND_KEY, CHAT_CUSTOM_BACKGROUND_KEY]);
}
