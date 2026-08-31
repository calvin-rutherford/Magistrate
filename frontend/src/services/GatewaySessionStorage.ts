import * as SecureStore from 'expo-secure-store';
import { Platform } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';

/**
 * The bearer session is a trusted-device secret. Native builds use the iOS
 * Keychain/Android Keystore through SecureStore and never fall back to
 * AsyncStorage when that native mechanism is unavailable (for example, an
 * incorrectly provisioned client).
 *
 * SecureStore has no web implementation. The browser fallback uses the
 * existing web session key directly (not the AsyncStorage abstraction); it is
 * not a native-device credential store.
 */
export const GATEWAY_SESSION_STORAGE_KEY = 'magistrate.gateway.session.v1';
const LEGACY_ASYNC_STORAGE_KEY = 'magistrate.gateway.session';
const SECURE_STORE_OPTIONS: SecureStore.SecureStoreOptions = {
  keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
};

function browserSessionStorage(): Storage | null {
  return typeof window === 'undefined' ? null : window.localStorage;
}

export async function getGatewaySessionPayload(): Promise<string | null> {
  if (Platform.OS === 'web') return browserSessionStorage()?.getItem(LEGACY_ASYNC_STORAGE_KEY) || null;
  if (!(await SecureStore.isAvailableAsync())) return null;
  return SecureStore.getItemAsync(GATEWAY_SESSION_STORAGE_KEY, SECURE_STORE_OPTIONS);
}

export async function setGatewaySessionPayload(payload: string): Promise<void> {
  if (Platform.OS === 'web') {
    browserSessionStorage()?.setItem(LEGACY_ASYNC_STORAGE_KEY, payload);
    return;
  }
  if (!(await SecureStore.isAvailableAsync())) throw new Error('Secure session storage is unavailable on this device.');
  await SecureStore.setItemAsync(GATEWAY_SESSION_STORAGE_KEY, payload, SECURE_STORE_OPTIONS);
}

export async function clearGatewaySessionPayload(): Promise<void> {
  if (Platform.OS === 'web') {
    browserSessionStorage()?.removeItem(LEGACY_ASYNC_STORAGE_KEY);
    return;
  }
  if (await SecureStore.isAvailableAsync()) await SecureStore.deleteItemAsync(GATEWAY_SESSION_STORAGE_KEY, SECURE_STORE_OPTIONS);
}

/** Remove the pre-Keychain value without ever treating it as a session. */
export async function removeLegacyGatewaySessionPayload(): Promise<void> {
  if (Platform.OS === 'web') return;
  await AsyncStorage.removeItem(LEGACY_ASYNC_STORAGE_KEY).catch(() => undefined);
}
