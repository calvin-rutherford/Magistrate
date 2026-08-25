export const Colors = {
  background: '#0D1322',
  surface: 'rgba(255, 255, 255, 0.08)',
  surfaceBorder: 'rgba(255, 255, 255, 0.18)',
  primary: '#34D399',
  emerald: '#34D399',
  amber: '#F59E0B',
  sky: '#38BDF8',
  violet: '#A855F7',
  danger: '#EF4444',
  textPrimary: '#FFFFFF',
  textSecondary: '#94A3B8',
  textMuted: '#64748B',
  status: {
    working: '#34D399',
    blocked: '#F59E0B',
    done: '#38BDF8',
    idle: '#A855F7',
    unknown: '#64748B'
  }
};

const themeObj = {
  background: '#0D1322',
  surface: 'rgba(255, 255, 255, 0.08)',
  border: 'rgba(255, 255, 255, 0.18)',
  primary: '#34D399',
  accent: '#A855F7',
  textPrimary: '#FFFFFF',
  textSecondary: '#94A3B8',
  cardBackground: 'rgba(255, 255, 255, 0.08)',
  primaryBackground: '#0D1322',
  danger: '#EF4444',
  success: '#34D399'
};

export const Theme = {
  light: themeObj,
  dark: themeObj,
  ...Colors,
  ...themeObj
};
