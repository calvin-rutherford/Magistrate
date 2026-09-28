import type { ExpoConfig } from 'expo/config';

/**
 * Build-time-only public configuration. The gateway URL is intentionally not a
 * credential; secrets and runner addresses stay on the Gateway host. EAS
 * environments should provide EXPO_PUBLIC_GATEWAY_URL for each profile.
 */
export default ({ config }: { config: ExpoConfig }): ExpoConfig => {
  const gatewayUrl = process.env.EXPO_PUBLIC_GATEWAY_URL?.trim();
  const easProjectId = process.env.EXPO_PUBLIC_EAS_PROJECT_ID?.trim();
  const googleIosScheme = process.env.EXPO_PUBLIC_GOOGLE_IOS_REVERSED_CLIENT_ID?.trim();
  const googleIosClientId = process.env.EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID?.trim();
  const storeBuild = process.env.EAS_BUILD_PROFILE === 'production' || process.env.EXPO_PUBLIC_BUILD_PROFILE === 'production';
  const appleTeamId = process.env.APPLE_TEAM_ID?.trim();
  const privacyUrl = process.env.EXPO_PUBLIC_PRIVACY_URL?.trim();
  const supportUrl = process.env.EXPO_PUBLIC_SUPPORT_URL?.trim();
  if (storeBuild && (!gatewayUrl || !appleTeamId || !privacyUrl || !supportUrl)) {
    throw new Error('Store builds require an explicit Gateway, APPLE_TEAM_ID, privacy URL and support URL.');
  }
  if (appleTeamId && !/^[A-Z0-9]{10}$/.test(appleTeamId)) {
    throw new Error('APPLE_TEAM_ID must be the Apple Developer team identifier.');
  }
  for (const value of [privacyUrl, supportUrl]) {
    if (!value) continue;
    const url = new URL(value);
    if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash
      || !url.hostname.includes('.') || /(?:^|\.)(?:localhost|local|internal|invalid|test|example)$/.test(url.hostname)
      || /^\d+(?:\.\d+){3}$/.test(url.hostname) || url.hostname.includes(':')) {
      throw new Error('Privacy and support URLs must be public credential-free HTTPS pages.');
    }
  }

  if (gatewayUrl) {
    const parsed = new URL(gatewayUrl);
    const local = ['localhost', '127.0.0.1', '[::1]'].includes(parsed.hostname);
    if (storeBuild && (/^\d+(?:\.\d+){3}$/.test(parsed.hostname) || parsed.hostname.includes(':')
      || /(?:^|\.)(?:localhost|local|internal|invalid|test|example)$/.test(parsed.hostname))) {
      throw new Error('Store builds require a public Gateway DNS name.');
    }
    const productionBuild = process.env.EAS_BUILD_PROFILE === 'production' || process.env.EXPO_PUBLIC_BUILD_PROFILE === 'production' || process.env.NODE_ENV === 'production';
    if ((parsed.protocol !== 'https:' && !local) || (productionBuild && (parsed.protocol !== 'https:' || local))) {
      throw new Error('EXPO_PUBLIC_GATEWAY_URL must use a public HTTPS endpoint for production builds.');
    }
    if (parsed.username || parsed.password || parsed.search || parsed.hash) {
      throw new Error('EXPO_PUBLIC_GATEWAY_URL must not contain credentials, query parameters, or a fragment.');
    }
    if (!parsed.pathname.endsWith('/api/v1')) {
      throw new Error('EXPO_PUBLIC_GATEWAY_URL must end with /api/v1.');
    }
  }

  if (easProjectId && !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(easProjectId)) {
    throw new Error('EXPO_PUBLIC_EAS_PROJECT_ID must be a UUID from the linked EAS project.');
  }
  if (googleIosScheme && !/^com\.googleusercontent\.apps\.[A-Za-z0-9._-]+$/.test(googleIosScheme)) {
    throw new Error('EXPO_PUBLIC_GOOGLE_IOS_REVERSED_CLIENT_ID is invalid.');
  }
  if (googleIosClientId) {
    const match = /^([A-Za-z0-9._-]+)\.apps\.googleusercontent\.com$/.exec(googleIosClientId);
    if (!match) throw new Error('EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID is invalid.');
    if (googleIosScheme && googleIosScheme !== `com.googleusercontent.apps.${match[1]}`) {
      throw new Error('The reversed Google iOS client ID does not match EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID.');
    }
  }
  if (Boolean(googleIosScheme) !== Boolean(googleIosClientId)) {
    throw new Error('Google iOS client ID and reversed callback scheme must be configured together.');
  }
  const configuredSchemes = Array.isArray(config.scheme)
    ? config.scheme : config.scheme ? [config.scheme] : [];

  return {
    ...config,
    owner: process.env.EXPO_OWNER?.trim() || config.owner,
    scheme: googleIosScheme
      ? [...new Set([...configuredSchemes, googleIosScheme])]
      : config.scheme,
    runtimeVersion: { policy: 'appVersion' },
    // OTA is not installed/configured. Rollback means a reviewed store build,
    // not silently loading unsigned remote JavaScript.
    updates: { ...config.updates, enabled: false },
    ios: {
      ...config.ios,
      ...(appleTeamId ? { appleTeamId } : {}),
      infoPlist: {
        ...config.ios?.infoPlist,
        NSAppTransportSecurity: { NSAllowsArbitraryLoads: false },
      },
    },
    extra: {
      ...config.extra,
      ...(gatewayUrl ? { gateway: { url: gatewayUrl } } : {}),
      ...(easProjectId ? { eas: { projectId: easProjectId } } : {}),
      ...(privacyUrl && supportUrl ? { legal: { privacyUrl, supportUrl } } : {}),
    },
  };
};
