import React from 'react';
import Svg, { Circle, Path, Rect } from 'react-native-svg';

// One optical size and one stroke weight for every navigation and control
// glyph, so the shell reads as a single icon system (brand guide 23). Sizes are
// passed by the caller; the 24-unit viewBox keeps the stroke visually constant.
export const ICON_SIZE = 24;
const STROKE = 1.6;

export type IconProps = { color: string; size?: number; testID?: string };

const icon = (children: React.ReactNode) => function MagistrateIcon({ color, size = ICON_SIZE, testID }: IconProps) {
  return <Svg testID={testID} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={STROKE} strokeLinecap="round" strokeLinejoin="round">{children}</Svg>;
};

/** Two rules rather than three: the reference drawer controls read calmer. */
export const MenuIcon = icon(<><Path d="M4 9h16" /><Path d="M4 15h16" /></>);
export const ComposeIcon = icon(<><Path d="M4 20h4l10.5-10.5a2.1 2.1 0 0 0-3-3L5 17v3z" /><Path d="M13.5 6.5l4 4" /></>);
export const ChevronDownIcon = icon(<Path d="m6 9.5 6 6 6-6" />);
export const ChevronRightIcon = icon(<Path d="m9.5 6 6 6-6 6" />);
export const SearchIcon = icon(<><Circle cx="11" cy="11" r="6.4" /><Path d="m16 16 4 4" /></>);
export const HomeIcon = icon(<><Path d="M4 10.5 12 4l8 6.5" /><Path d="M6 9.5V20h12V9.5" /></>);
export const FleetIcon = icon(<><Rect x="3.5" y="4.5" width="17" height="6" rx="2" /><Rect x="3.5" y="13.5" width="17" height="6" rx="2" /><Path d="M7 7.5h.01M7 16.5h.01" /></>);
export const AttentionIcon = icon(<><Circle cx="12" cy="12" r="8.4" /><Path d="M12 7.8v4.6" /><Path d="M12 16.1h.01" /></>);
export const ActivityIcon = icon(<Path d="M3.5 12.5h4l2.5-6 4 12 2.5-6h4" />);
export const ProjectsIcon = icon(<><Path d="M3.5 7.5a2 2 0 0 1 2-2h3.2l1.8 2.2h8a2 2 0 0 1 2 2v7.8a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z" /></>);
export const ConnectionsIcon = icon(<><Circle cx="6.5" cy="12" r="2.6" /><Circle cx="17.5" cy="6.8" r="2.6" /><Circle cx="17.5" cy="17.2" r="2.6" /><Path d="m8.9 10.9 6.2-2.9M8.9 13.1l6.2 2.9" /></>);
export const AccountIcon = icon(<><Circle cx="12" cy="8.6" r="3.6" /><Path d="M5.4 19.4a6.9 6.9 0 0 1 13.2 0" /></>);
export const SlidersIcon = icon(<><Path d="M4 8h9M17 8h3M4 16h3M11 16h9" /><Circle cx="15" cy="8" r="2" /><Circle cx="9" cy="16" r="2" /></>);
export const BellIcon = icon(<><Path d="M6.5 10a5.5 5.5 0 0 1 11 0c0 4 1.5 5.5 1.5 5.5H5S6.5 14 6.5 10z" /><Path d="M10.2 18.5a2 2 0 0 0 3.6 0" /></>);
export const PaletteIcon = icon(<><Path d="M12 3.6a8.4 8.4 0 1 0 0 16.8c1.2 0 1.8-.8 1.8-1.7 0-1.5-1-1.9-1-3 0-.9.7-1.6 1.7-1.6h1.6a4.3 4.3 0 0 0 4.3-4.3c0-3.4-3.6-6.2-8.4-6.2z" /><Circle cx="8.4" cy="10.2" r="1" /><Circle cx="12" cy="7.8" r="1" /><Circle cx="15.6" cy="9.9" r="1" /></>);
export const ShieldIcon = icon(<Path d="M12 3.8 5.5 6.4v5c0 4 2.8 7.1 6.5 8.8 3.7-1.7 6.5-4.8 6.5-8.8v-5z" />);
export const InfoIcon = icon(<><Circle cx="12" cy="12" r="8.4" /><Path d="M12 11v5.2" /><Path d="M12 8h.01" /></>);
export const StopIcon = icon(<Rect x="7.5" y="7.5" width="9" height="9" rx="2" />);
export const ArrowUpIcon = icon(<><Path d="M12 19V5.6" /><Path d="m6.2 11.4 5.8-5.8 5.8 5.8" /></>);
export const CloseIcon = icon(<Path d="m6.6 6.6 10.8 10.8M17.4 6.6 6.6 17.4" />);
