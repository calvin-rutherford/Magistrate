import * as AppleAuthentication from 'expo-apple-authentication';
import * as AuthSession from 'expo-auth-session';
import * as Crypto from 'expo-crypto';
import * as WebBrowser from 'expo-web-browser';
import { Platform } from 'react-native';
import {
  createProviderAuthChallenge,
  exchangeProviderAuthChallenge,
  ProviderAuthChallenge,
  validateGatewaySession,
} from '../api/client';

WebBrowser.maybeCompleteAuthSession();

type Provider = 'apple' | 'google';

const GOOGLE_DISCOVERY: AuthSession.DiscoveryDocument = {
  authorizationEndpoint: 'https://accounts.google.com/o/oauth2/v2/auth',
  tokenEndpoint: 'https://oauth2.googleapis.com/token',
  revocationEndpoint: 'https://oauth2.googleapis.com/revoke',
};
const APPLE_DISCOVERY: AuthSession.DiscoveryDocument = {
  authorizationEndpoint: 'https://appleid.apple.com/auth/authorize',
  tokenEndpoint: 'https://appleid.apple.com/auth/token',
};
const prepared = new Map<Provider, Promise<ProviderAuthChallenge>>();
let activeSignIn: Promise<void> | null = null;

function cancelled(provider: Provider): Error & { code: string } {
  return Object.assign(
    new Error(`${provider === 'apple' ? 'Apple' : 'Google'} sign-in was cancelled.`),
    { code: 'ERR_REQUEST_CANCELED' },
  );
}

async function providerAuthorizationNonce(provider: Provider, rawNonce: string): Promise<string> {
  if (provider !== 'apple') return rawNonce;
  // Apple carries the SHA-256 digest in its ID token. The raw one-time nonce
  // stays private to the challenge exchange so the Gateway can verify both.
  return Crypto.digestStringAsync(Crypto.CryptoDigestAlgorithm.SHA256, rawNonce);
}

function providerClientId(provider: Provider): string | null {
  if (provider === 'apple') {
    return Platform.OS === 'web'
      ? process.env.EXPO_PUBLIC_APPLE_SERVICE_ID?.trim() || null
      : 'io.magistrate.cockpit';
  }
  return (Platform.OS === 'web'
    ? process.env.EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID
    : process.env.EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID)?.trim() || null;
}

function redirectUri(provider: Provider): string | undefined {
  if (Platform.OS === 'web') {
    if (typeof window === 'undefined') return undefined;
    return `${window.location.origin}/`;
  }
  if (provider === 'google') {
    const reversed = process.env.EXPO_PUBLIC_GOOGLE_IOS_REVERSED_CLIENT_ID?.trim();
    if (!reversed) return undefined;
    return AuthSession.makeRedirectUri({
      native: `${reversed}:/oauthredirect`, scheme: reversed, path: 'oauthredirect',
    });
  }
  return undefined;
}

export function providerSignInAvailable(provider: Provider): boolean {
  if (provider === 'apple') {
    return Platform.OS === 'ios' || (Platform.OS === 'web' && Boolean(providerClientId('apple')));
  }
  return (Platform.OS === 'ios' || Platform.OS === 'web')
    && Boolean(providerClientId('google'))
    && (Platform.OS === 'web' || Boolean(process.env.EXPO_PUBLIC_GOOGLE_IOS_REVERSED_CLIENT_ID?.trim()));
}

export function prepareProviderSignIn(provider: Provider): void {
  if (!providerSignInAvailable(provider) || prepared.has(provider)) return;
  const redirect = redirectUri(provider);
  const challenge = createProviderAuthChallenge(provider, redirect)
    .catch(error => { prepared.delete(provider); throw error; });
  // Preloading avoids a network wait between a browser click and popup open.
  // Keep rejected preparation handled until an explicit sign-in reports it.
  void challenge.catch(() => undefined);
  prepared.set(provider, challenge);
}

async function takeChallenge(provider: Provider): Promise<ProviderAuthChallenge> {
  let pending = prepared.get(provider);
  if (!pending) {
    prepareProviderSignIn(provider);
    pending = prepared.get(provider);
  }
  if (!pending) throw new Error(`${provider === 'apple' ? 'Apple' : 'Google'} sign-in is not configured for this client.`);
  prepared.delete(provider);
  let challenge = await pending;
  // A page can remain open longer than the server's one-time nonce lifetime.
  // Never send a user through provider UI with a challenge already near expiry.
  if (challenge.expires_at <= Math.floor(Date.now() / 1000) + 30) {
    challenge = await createProviderAuthChallenge(provider, redirectUri(provider));
  }
  return challenge;
}

function appleDisplayName(fullName: AppleAuthentication.AppleAuthenticationFullName | null): string | undefined {
  if (!fullName) return undefined;
  const value = [fullName.givenName, fullName.middleName, fullName.familyName]
    .filter((part): part is string => Boolean(part?.trim())).join(' ').trim();
  return value || undefined;
}

async function signInWithNativeApple(challenge: ProviderAuthChallenge): Promise<void> {
  if (!(await AppleAuthentication.isAvailableAsync())) {
    throw new Error('Sign in with Apple is unavailable on this device.');
  }
  const credential = await AppleAuthentication.signInAsync({
    requestedScopes: [
      AppleAuthentication.AppleAuthenticationScope.FULL_NAME,
      AppleAuthentication.AppleAuthenticationScope.EMAIL,
    ],
    state: challenge.challenge_id,
    nonce: await providerAuthorizationNonce('apple', challenge.authorization_nonce),
  });
  if (credential.state !== challenge.challenge_id || !credential.identityToken) {
    throw new Error('Apple returned an invalid sign-in response.');
  }
  await exchangeProviderAuthChallenge({
    provider: 'apple', challengeId: challenge.challenge_id, nonce: challenge.nonce,
    identityToken: credential.identityToken,
    displayName: appleDisplayName(credential.fullName),
  });
}

async function signInWithWebApple(challenge: ProviderAuthChallenge): Promise<void> {
  const clientId = providerClientId('apple');
  const redirect = challenge.redirect_uri || redirectUri('apple');
  if (!clientId || !redirect) throw new Error('Apple web sign-in is not configured.');
  const request = new AuthSession.AuthRequest({
    clientId, redirectUri: redirect, responseType: AuthSession.ResponseType.Code,
    scopes: [], state: challenge.challenge_id, usePKCE: false,
    extraParams: {
      nonce: await providerAuthorizationNonce('apple', challenge.authorization_nonce),
      response_mode: 'query',
    },
  });
  const result = await request.promptAsync(APPLE_DISCOVERY);
  if (result.type === 'cancel' || result.type === 'dismiss') throw cancelled('apple');
  if (result.type !== 'success' || !result.params.code || result.params.state !== challenge.challenge_id) {
    throw new Error('Apple sign-in could not be completed.');
  }
  await exchangeProviderAuthChallenge({
    provider: 'apple', challengeId: challenge.challenge_id, nonce: challenge.nonce,
    authorizationCode: result.params.code, redirectUri: redirect,
  });
}

async function signInWithGoogle(challenge: ProviderAuthChallenge): Promise<void> {
  const clientId = providerClientId('google');
  const redirect = challenge.redirect_uri || redirectUri('google');
  if (!clientId || !redirect) throw new Error('Google sign-in is not configured for this client.');
  const web = Platform.OS === 'web';
  const request = new AuthSession.AuthRequest({
    clientId, redirectUri: redirect,
    responseType: web ? AuthSession.ResponseType.IdToken : AuthSession.ResponseType.Code,
    scopes: ['openid', 'profile', 'email'], state: challenge.challenge_id,
    prompt: AuthSession.Prompt.SelectAccount, usePKCE: !web,
    extraParams: { nonce: challenge.authorization_nonce },
  });
  const result = await request.promptAsync(GOOGLE_DISCOVERY);
  if (result.type === 'cancel' || result.type === 'dismiss') throw cancelled('google');
  if (result.type !== 'success' || result.params.state !== challenge.challenge_id) {
    throw new Error('Google sign-in could not be completed.');
  }
  let identityToken = result.params.id_token || result.authentication?.idToken;
  if (!web) {
    if (!result.params.code || !request.codeVerifier) throw new Error('Google returned an invalid authorization response.');
    const tokens = await AuthSession.exchangeCodeAsync({
      clientId, code: result.params.code, redirectUri: redirect,
      extraParams: { code_verifier: request.codeVerifier },
    }, GOOGLE_DISCOVERY);
    identityToken = tokens.idToken;
  }
  if (!identityToken) throw new Error('Google did not return an identity assertion.');
  await exchangeProviderAuthChallenge({
    provider: 'google', challengeId: challenge.challenge_id, nonce: challenge.nonce,
    identityToken, redirectUri: redirect,
  });
}

export function signInWithProvider(provider: Provider): Promise<void> {
  if (activeSignIn) return activeSignIn;
  activeSignIn = (async () => {
    if (!providerSignInAvailable(provider)) {
      throw new Error(`${provider === 'apple' ? 'Apple' : 'Google'} sign-in is not configured for this client.`);
    }
    const challenge = await takeChallenge(provider);
    try {
      if (provider === 'apple') {
        if (Platform.OS === 'ios') await signInWithNativeApple(challenge);
        else await signInWithWebApple(challenge);
      } else {
        await signInWithGoogle(challenge);
      }
      await validateGatewaySession();
    } finally {
      prepareProviderSignIn(provider);
    }
  })().finally(() => { activeSignIn = null; });
  return activeSignIn;
}
