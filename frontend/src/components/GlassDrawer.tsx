import React from 'react';
import { Modal, ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useChatColorScheme } from '../services/ChatPreferences';
import { ActivityIcon, AccountIcon, AttentionIcon, CloseIcon, FleetIcon, HomeIcon, ProjectsIcon } from './MagistrateIcons';

interface GlassDrawerProps {
  visible: boolean;
  onClose: () => void;
  onNavigate: (route: string) => void;
  attentionCount: number;
  /** Retained for callers that still fetch this count; unfinished PR navigation is intentionally hidden. */
  prsCount?: number;
}

const rows = [
  { route: 'chat', label: 'Chat', Icon: HomeIcon },
  { route: 'chat?section=projects', label: 'Projects', Icon: ProjectsIcon },
  { route: 'chat?section=fleet', label: 'Work', Icon: FleetIcon },
  { route: 'chat?section=activity', label: 'Activity', Icon: ActivityIcon },
] as const;

/** Opaque, independently scrolling navigation used by routes outside Chat. */
export const GlassDrawer: React.FC<GlassDrawerProps> = ({
  visible, onClose, onNavigate, attentionCount, prsCount: _prsCount,
}) => {
  const dark = useChatColorScheme() === 'dark';
  const insets = useSafeAreaInsets();
  const text = dark ? '#F4F5F7' : '#11151B';
  const muted = dark ? '#8E99AA' : '#667180';
  const edge = dark ? '#303238' : '#D9DCE1';
  if (!visible) return null;

  const navigate = (route: string) => { onNavigate(route); onClose(); };
  return (
    <Modal visible={visible} animationType="fade" transparent onRequestClose={onClose}>
      <View style={styles.layer}>
        <TouchableOpacity accessibilityRole="button" accessibilityLabel="Close navigation" style={styles.scrim} activeOpacity={1} onPress={onClose} />
        <View style={[styles.drawer, { paddingTop: Math.max(16, insets.top), paddingBottom: Math.max(12, insets.bottom), backgroundColor: dark ? '#111214' : '#FFFFFF', borderColor: edge }]}>
          <View style={styles.header}>
            <Text accessibilityRole="header" style={[styles.title, { color: text }]}>Menu</Text>
            <TouchableOpacity accessibilityRole="button" accessibilityLabel="Close navigation" onPress={onClose} style={[styles.close, { borderColor: edge }]}>
              <CloseIcon color={text} size={20} />
            </TouchableOpacity>
          </View>
          <ScrollView style={styles.scroller} contentContainerStyle={styles.scrollContent} keyboardShouldPersistTaps="handled">
            {rows.map(({ route, label, Icon }) => (
              <TouchableOpacity key={route} accessibilityRole="button" accessibilityLabel={`Open ${label}`} style={[styles.row, { borderBottomColor: edge }]} onPress={() => navigate(route)}>
                <Icon color={muted} size={22} />
                <Text style={[styles.rowText, { color: text }]}>{label}</Text>
              </TouchableOpacity>
            ))}
            <TouchableOpacity accessibilityRole="button" accessibilityLabel="Open Attention" style={[styles.row, { borderBottomColor: edge }]} onPress={() => navigate('attention')}>
              <AttentionIcon color={attentionCount > 0 ? '#C87A10' : muted} size={22} />
              <Text style={[styles.rowText, { color: text }]}>Attention</Text>
              {attentionCount > 0 ? <Text accessibilityLabel={`${attentionCount} items`} style={styles.count}>{attentionCount}</Text> : null}
            </TouchableOpacity>
          </ScrollView>
          <TouchableOpacity accessibilityRole="button" accessibilityLabel="Open Account and Settings" onPress={() => navigate('account')} style={[styles.settings, { borderColor: edge }]}>
            <AccountIcon color={text} size={22} />
            <Text style={[styles.settingsText, { color: text }]}>Account & Settings</Text>
          </TouchableOpacity>
        </View>
      </View>
    </Modal>
  );
};

const styles = StyleSheet.create({
  layer: { flex: 1 },
  scrim: { ...StyleSheet.absoluteFill, backgroundColor: 'rgba(5,7,10,0.48)' },
  drawer: { width: '84%', maxWidth: 360, height: '100%', borderRightWidth: StyleSheet.hairlineWidth, paddingHorizontal: 18 },
  header: { minHeight: 52, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 },
  title: { fontSize: 24, lineHeight: 31, fontWeight: '600', letterSpacing: -0.3 },
  close: { width: 44, height: 44, borderRadius: 22, borderWidth: StyleSheet.hairlineWidth, alignItems: 'center', justifyContent: 'center' },
  scroller: { flex: 1, minHeight: 0 },
  scrollContent: { paddingBottom: 24 },
  row: { minHeight: 56, flexDirection: 'row', alignItems: 'center', gap: 14, borderBottomWidth: StyleSheet.hairlineWidth },
  rowText: { flex: 1, fontSize: 16, lineHeight: 22, fontWeight: '500' },
  count: { minWidth: 24, textAlign: 'center', color: '#8B4F00', backgroundColor: '#FFDDA8', borderRadius: 12, overflow: 'hidden', paddingHorizontal: 7, paddingVertical: 2, fontSize: 12, fontWeight: '700' },
  settings: { minHeight: 52, flexDirection: 'row', alignItems: 'center', gap: 12, borderWidth: StyleSheet.hairlineWidth, borderRadius: 18, paddingHorizontal: 15 },
  settingsText: { fontSize: 15, fontWeight: '600' },
});
