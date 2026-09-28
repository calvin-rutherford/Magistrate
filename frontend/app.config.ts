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

  if (gatewayUrl) {
    const parsed = new URL(gatewayUrl);
    const local = ['localhost', '127.0.0.1', '[::1]'].includes(parsed.hostname);
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
    extra: {
      ...config.extra,
      ...(gatewayUrl ? { gateway: { url: gatewayUrl } } : {}),
      ...(easProjectId ? { eas: { projectId: easProjectId } } : {}),
    },
  };
};
