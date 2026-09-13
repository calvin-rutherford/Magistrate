#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND_ROOT = path.resolve(HERE, '..');
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const ASC_APP_ID = /^[1-9][0-9]{5,14}$/;
const SECRET_PUBLIC_NAME = /(secret|token|password|credential|api[_-]?key|private[_-]?key)/i;

function readJson(name) {
  return JSON.parse(fs.readFileSync(path.join(FRONTEND_ROOT, name), 'utf8'));
}

function booleanLiteral(value) {
  const normalized = String(value || '').trim().toLowerCase();
  if (['1', 'true', 'yes', 'on'].includes(normalized)) return true;
  if (['0', 'false', 'no', 'off'].includes(normalized)) return false;
  return null;
}

export function evaluateFriendBetaRelease({ profile, env, app, eas, packageJson }) {
  const failures = [];
  const checks = [];
  const check = (condition, id, failure) => {
    if (condition) checks.push(id);
    else failures.push(`${id}: ${failure}`);
  };

  check(['development', 'preview', 'production'].includes(profile), 'profile-known', 'profile must be development, preview, or production');
  const expo = app?.expo || {};
  check(typeof expo.owner === 'string' && expo.owner.trim().length > 0, 'eas-owner-linked', 'app.json must name the linked Expo owner');
  check(UUID.test(expo?.extra?.eas?.projectId || ''), 'eas-project-linked', 'app.json must contain the linked EAS project UUID');
  check(expo?.ios?.bundleIdentifier === 'io.magistrate.cockpit', 'ios-bundle-id', 'the release bundle identifier changed unexpectedly');
  check(expo?.ios?.supportsTablet === false, 'iphone-only-beta', 'Friend Beta is scoped to tested iPhone devices, not iPad');
  check(typeof expo.version === 'string' && /^\d+\.\d+\.\d+$/.test(expo.version), 'app-version', 'expo.version must be semantic x.y.z');
  check(typeof expo?.ios?.buildNumber === 'string' && /^[1-9][0-9]*$/.test(expo.ios.buildNumber), 'ios-build-number', 'ios.buildNumber must be a positive integer string');
  check(expo?.ios?.config?.usesNonExemptEncryption === false, 'export-compliance', 'ios.config.usesNonExemptEncryption must explicitly be false');

  const backgroundModes = expo?.ios?.infoPlist?.UIBackgroundModes;
  check(Array.isArray(backgroundModes)
    && backgroundModes.length === 1 && backgroundModes[0] === 'remote-notification',
  'ios-background-modes', 'Friend Beta may declare only one remote-notification background mode');
  check(Array.isArray(expo.plugins) && expo.plugins.some(plugin => plugin === 'expo-secure-store'
    || (Array.isArray(plugin) && plugin[0] === 'expo-secure-store')),
  'secure-store-plugin', 'expo-secure-store config plugin is required');
  check(typeof packageJson?.dependencies?.['expo-secure-store'] === 'string', 'secure-store-package', 'expo-secure-store dependency is required');
  check(typeof packageJson?.dependencies?.['expo-dev-client'] === 'string', 'development-client-package', 'expo-dev-client dependency is required for device development builds');
  check(packageJson?.scripts?.['eas-build-pre-install'] === 'node scripts/friend-beta-release-preflight.mjs --profile "$EAS_BUILD_PROFILE"',
    'eas-pre-install-gate', 'EAS builds must invoke this preflight for their selected profile');

  check(eas?.cli?.requireCommit === true, 'eas-clean-commit', 'EAS must refuse uncommitted release inputs');
  check(eas?.cli?.appVersionSource === 'remote', 'eas-remote-version', 'EAS must own monotonic store build numbers');
  const build = eas?.build?.[profile];
  check(Boolean(build), 'eas-profile', `eas.json is missing build.${profile}`);
  check(booleanLiteral(build?.env?.EXPO_PUBLIC_MAGI_NATIVE_CHAT_ENABLED) === true
    && booleanLiteral(build?.env?.EXPO_PUBLIC_MAGI_LEGACY_CHAT_ENABLED) === false,
  'eas-native-only-env', `build.${profile}.env must commit the provider-native-only selection`);
  if (profile === 'development') {
    check(build?.developmentClient === true && build?.distribution === 'internal' && build?.ios?.simulator === false,
      'physical-development-profile', 'development must be an internal physical-device development client');
  }
  if (profile === 'preview') {
    check(build?.distribution === 'internal' && build?.ios?.simulator === false,
      'physical-preview-profile', 'preview must be an internal physical-device build');
  }
  if (profile === 'production') {
    check(build?.distribution === 'store' && build?.autoIncrement === true,
      'testflight-production-profile', 'production must be a store build with autoIncrement');
    check(ASC_APP_ID.test(eas?.submit?.production?.ios?.ascAppId || ''),
      'app-store-connect-link', 'set the public numeric ascAppId after the App Store Connect record exists');
  }

  const configuredOwner = String(env.EXPO_OWNER || '').trim();
  if (configuredOwner) check(configuredOwner === expo.owner, 'eas-owner-env', 'EXPO_OWNER does not match app.json');
  const configuredProject = String(env.EXPO_PUBLIC_EAS_PROJECT_ID || '').trim();
  if (configuredProject) check(configuredProject === expo?.extra?.eas?.projectId, 'eas-project-env', 'EXPO_PUBLIC_EAS_PROJECT_ID does not match app.json');

  const gatewayValue = String(env.EXPO_PUBLIC_GATEWAY_URL || '').trim();
  let gateway;
  try { gateway = new URL(gatewayValue); } catch { gateway = null; }
  check(Boolean(gateway), 'gateway-url-present', 'EXPO_PUBLIC_GATEWAY_URL must be an absolute URL');
  if (gateway) {
    const local = ['localhost', '127.0.0.1', '[::1]'].includes(gateway.hostname);
    check(gateway.protocol === 'https:' && !local, 'gateway-https-public', 'device builds require a non-local HTTPS Gateway');
    check(!gateway.username && !gateway.password && !gateway.search && !gateway.hash,
      'gateway-url-public-config', 'Gateway URL must not contain credentials, query parameters, or a fragment');
    check(gateway.pathname.endsWith('/api/v1'), 'gateway-api-root', 'Gateway URL must end with /api/v1');
  }

  check(booleanLiteral(env.EXPO_PUBLIC_MAGI_NATIVE_CHAT_ENABLED) === true,
    'native-chat-selected', 'set EXPO_PUBLIC_MAGI_NATIVE_CHAT_ENABLED=true in the EAS environment');
  check(booleanLiteral(env.EXPO_PUBLIC_MAGI_LEGACY_CHAT_ENABLED) === false,
    'legacy-chat-disabled', 'set EXPO_PUBLIC_MAGI_LEGACY_CHAT_ENABLED=false in the EAS environment');

  const unsafePublicNames = Object.keys(env).filter(name => name.startsWith('EXPO_PUBLIC_') && SECRET_PUBLIC_NAME.test(name));
  check(unsafePublicNames.length === 0, 'no-public-secret-names', `remove secret-like public variables: ${unsafePublicNames.join(', ')}`);

  return {
    schema_version: 'friend-beta-release-preflight.v1',
    profile,
    result: failures.length ? 'FAIL' : 'PASS',
    checks,
    failures,
  };
}

function argumentProfile(argv) {
  const index = argv.indexOf('--profile');
  if (index < 0 || !argv[index + 1]) throw new Error('usage: npm run beta:preflight -- --profile <development|preview|production>');
  return argv[index + 1];
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const result = evaluateFriendBetaRelease({
      profile: argumentProfile(process.argv.slice(2)),
      env: process.env,
      app: readJson('app.json'),
      eas: readJson('eas.json'),
      packageJson: readJson('package.json'),
    });
    const output = JSON.stringify(result);
    if (result.result === 'PASS') console.log(output);
    else console.error(output);
    process.exitCode = result.result === 'PASS' ? 0 : 1;
  } catch (error) {
    console.error(JSON.stringify({
      schema_version: 'friend-beta-release-preflight.v1',
      result: 'FAIL',
      failures: [error instanceof Error ? error.message : 'preflight failed'],
    }));
    process.exitCode = 1;
  }
}
