import AsyncStorage from '@react-native-async-storage/async-storage';
import { useEffect, useState } from 'react';
import { Appearance, useColorScheme } from 'react-native';
import { setActiveBackground, WeatherSceneKey } from './environmentTheme';
import { DEFAULT_VOICE_INPUT_MODE, VOICE_INPUT_MODE_KEY, VoiceInputMode } from './VoiceInputModes';

export const TOOL_CALL_VISIBILITY_KEY = 'magistrate.chat.show-tool-calls';
export const CHAT_THEME_MODE_KEY = 'magistrate.chat.theme-mode';
export const CHAT_BACKGROUND_KEY = 'magistrate.chat.background';
export const CHAT_CUSTOM_BACKGROUND_KEY = 'magistrate.chat.custom-background';
export const VOICE_CAPTURE_BEHAVIOR_KEY = 'magistrate.voice.capture-behavior';
export const VOICE_TRANSCRIPT_BEHAVIOR_KEY = 'magistrate.voice.transcript-behavior';
export type VoiceCaptureBehavior = 'tap-to-toggle' | 'hold-to-talk';
export type VoiceTranscriptBehavior = 'insert' | 'auto-send';

export type ChatThemeMode = 'system' | 'dark' | 'light';

export type ChatPreferences = {
  showToolCalls: boolean;
  themeMode: ChatThemeMode;
  background: WeatherSceneKey;
  customBackgroundUri?: string;
  voiceInputMode: VoiceInputMode;
  voiceCaptureBehavior: VoiceCaptureBehavior;
  voiceTranscriptBehavior: VoiceTranscriptBehavior;
};

export const DEFAULT_CHAT_PREFERENCES: ChatPreferences = {
  showToolCalls: false,
  themeMode: 'system',
  background: 'auto',
  customBackgroundUri: undefined,
  voiceInputMode: DEFAULT_VOICE_INPUT_MODE,
  voiceCaptureBehavior: 'tap-to-toggle',
  voiceTranscriptBehavior: 'insert',
};

const validThemeModes = new Set<ChatThemeMode>(['system', 'dark', 'light']);
const validVoiceInputModes = new Set<VoiceInputMode>(['automatic', 'browser', 'native', 'openai']);
const validVoiceCaptureBehaviors = new Set<VoiceCaptureBehavior>(['tap-to-toggle', 'hold-to-talk']);
const validVoiceTranscriptBehaviors = new Set<VoiceTranscriptBehavior>(['insert', 'auto-send']);
const validBackgrounds = new Set<WeatherSceneKey>([
  'auto', 'dusk-mountain', 'clear-day', 'clear-night', 'clouds', 'rain', 'storm', 'sunset', 'minimal-dark', 'custom',
]);

export async function loadChatPreferences(): Promise<ChatPreferences> {
  const [toolCalls, themeMode, background, customBackground, voiceInputMode, captureBehavior, transcriptBehavior] = await AsyncStorage.multiGet([
    TOOL_CALL_VISIBILITY_KEY, CHAT_THEME_MODE_KEY, CHAT_BACKGROUND_KEY, CHAT_CUSTOM_BACKGROUND_KEY,
    VOICE_INPUT_MODE_KEY, VOICE_CAPTURE_BEHAVIOR_KEY, VOICE_TRANSCRIPT_BEHAVIOR_KEY,
  ]);
  const storedBackground = validBackgrounds.has(background[1] as WeatherSceneKey)
    ? background[1] as WeatherSceneKey
    : DEFAULT_CHAT_PREFERENCES.background;
  // A custom scene is only valid when its persisted image is still present.
  // Otherwise a removed/expired upload must not resurrect stale presentation
  // state after a refresh.
  const hasCustomBackground = storedBackground === 'custom' && Boolean(customBackground[1]);
  const preferences: ChatPreferences = {
    showToolCalls: toolCalls[1] === 'true',
    themeMode: validThemeModes.has(themeMode[1] as ChatThemeMode) ? themeMode[1] as ChatThemeMode : DEFAULT_CHAT_PREFERENCES.themeMode,
    background: hasCustomBackground ? 'custom' : storedBackground === 'custom' ? DEFAULT_CHAT_PREFERENCES.background : storedBackground,
    customBackgroundUri: hasCustomBackground ? customBackground[1] || undefined : undefined,
    voiceInputMode: validVoiceInputModes.has(voiceInputMode[1] as VoiceInputMode) ? voiceInputMode[1] as VoiceInputMode : DEFAULT_CHAT_PREFERENCES.voiceInputMode,
    voiceCaptureBehavior: validVoiceCaptureBehaviors.has(captureBehavior[1] as VoiceCaptureBehavior) ? captureBehavior[1] as VoiceCaptureBehavior : DEFAULT_CHAT_PREFERENCES.voiceCaptureBehavior,
    voiceTranscriptBehavior: validVoiceTranscriptBehaviors.has(transcriptBehavior[1] as VoiceTranscriptBehavior) ? transcriptBehavior[1] as VoiceTranscriptBehavior : DEFAULT_CHAT_PREFERENCES.voiceTranscriptBehavior,
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

export async function saveVoiceInputMode(mode: VoiceInputMode): Promise<void> {
  if (!validVoiceInputModes.has(mode)) throw new Error('Unsupported voice input mode.');
  await AsyncStorage.setItem(VOICE_INPUT_MODE_KEY, mode);
}

export async function saveVoiceCaptureBehavior(value: VoiceCaptureBehavior): Promise<void> {
  if (!validVoiceCaptureBehaviors.has(value)) throw new Error('Unsupported voice capture behavior.');
  await AsyncStorage.setItem(VOICE_CAPTURE_BEHAVIOR_KEY, value);
}

export async function saveVoiceTranscriptBehavior(value: VoiceTranscriptBehavior): Promise<void> {
  if (!validVoiceTranscriptBehaviors.has(value)) throw new Error('Unsupported voice transcript behavior.');
  await AsyncStorage.setItem(VOICE_TRANSCRIPT_BEHAVIOR_KEY, value);
}

export async function saveThemeMode(themeMode: ChatThemeMode): Promise<void> {
  setThemeModeOverride(themeMode);
  await AsyncStorage.setItem(CHAT_THEME_MODE_KEY, themeMode);
}

export async function saveChatBackground(background: WeatherSceneKey): Promise<void> {
  setActiveBackground(background);
  if (background === 'custom') {
    await AsyncStorage.setItem(CHAT_BACKGROUND_KEY, background);
  } else {
    // Built-in choices supersede an uploaded image; do not leave a stale
    // custom URI that can reappear on the next hydration.
    await AsyncStorage.multiSet([[CHAT_BACKGROUND_KEY, background]]);
    await AsyncStorage.removeItem(CHAT_CUSTOM_BACKGROUND_KEY);
  }
}

export async function saveCustomBackground(uri: string): Promise<void> {
  setActiveBackground('custom', uri);
  await AsyncStorage.multiSet([[CHAT_BACKGROUND_KEY, 'custom'], [CHAT_CUSTOM_BACKGROUND_KEY, uri]]);
}

export async function removeCustomBackground(): Promise<void> {
  setActiveBackground('auto');
  await AsyncStorage.multiRemove([CHAT_BACKGROUND_KEY, CHAT_CUSTOM_BACKGROUND_KEY]);
}
