import AsyncStorage from '@react-native-async-storage/async-storage';

export type OperatingPermissionMode = 'restricted' | 'moderate' | 'full';
export const OPERATING_PERMISSION_MODE_KEY = 'magistrate.operating-permission-mode';

export interface OperatingPermissionModeOption {
  id: OperatingPermissionMode;
  label: string;
  description: string;
}

export const OPERATING_PERMISSION_MODE_OPTIONS: OperatingPermissionModeOption[] = [
  {
    id: 'restricted',
    label: 'Restricted / ask-first',
    description: 'Only decisions and blockers. Ask before any captain action.',
  },
  {
    id: 'moderate',
    label: 'Moderate',
    description: 'Adds review-ready PRs and meaningful milestones.',
  },
  {
    id: 'full',
    label: 'Full permission',
    description: 'Only stalls, failures, completions, and consequential decisions.',
  },
];

const validModes = new Set<OperatingPermissionMode>(OPERATING_PERMISSION_MODE_OPTIONS.map(option => option.id));

export function isOperatingPermissionMode(value: unknown): value is OperatingPermissionMode {
  return typeof value === 'string' && validModes.has(value as OperatingPermissionMode);
}

export async function loadOperatingPermissionMode(): Promise<OperatingPermissionMode> {
  const saved = await AsyncStorage.getItem(OPERATING_PERMISSION_MODE_KEY);
  return isOperatingPermissionMode(saved) ? saved : 'moderate';
}

export async function saveOperatingPermissionMode(mode: OperatingPermissionMode): Promise<void> {
  if (!isOperatingPermissionMode(mode)) throw new Error('Unsupported operating permission mode.');
  await AsyncStorage.setItem(OPERATING_PERMISSION_MODE_KEY, mode);
}
