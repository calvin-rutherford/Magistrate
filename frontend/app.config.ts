import type { ExpoConfig } from 'expo/config';

/**
 * Build-time-only public configuration. The gateway URL is intentionally not a
 * credential; secrets and runner addresses stay on the Gateway host. EAS
 * environments should provide EXPO_PUBLIC_GATEWAY_URL for each profile.
 */
export default ({ config }: { config: ExpoConfig }): ExpoConfig => {
  const gatewayUrl = process.env.EXPO_PUBLIC_GATEWAY_URL?.trim();
  const easProjectId = process.env.EXPO_PUBLIC_EAS_PROJECT_ID?.trim();

  if (gatewayUrl) {
    const parsed = new URL(gatewayUrl);
    const local = ['localhost', '127.0.0.1', '[::1]'].includes(parsed.hostname);
    const productionBuild = process.env.EAS_BUILD_PROFILE === 'production' || process.env.EXPO_PUBLIC_BUILD_PROFILE === 'production' || process.env.NODE_ENV === 'production';
    if ((parsed.protocol !== 'https:' && !local) || (productionBuild && (parsed.protocol !== 'https:' || local))) {
      throw new Error('EXPO_PUBLIC_GATEWAY_URL must use a public HTTPS endpoint for production builds.');
    }
    if (parsed.username || parsed.password) throw new Error('EXPO_PUBLIC_GATEWAY_URL must not contain credentials.');
    if (!parsed.pathname.endsWith('/api/v1')) {
      throw new Error('EXPO_PUBLIC_GATEWAY_URL must end with /api/v1.');
    }
  }

  return {
    ...config,
    owner: process.env.EXPO_OWNER?.trim() || config.owner,
    runtimeVersion: { policy: 'appVersion' },
    extra: {
      ...config.extra,
      ...(gatewayUrl ? { gateway: { url: gatewayUrl } } : {}),
      ...(easProjectId ? { eas: { projectId: easProjectId } } : {}),
    },
  };
};
