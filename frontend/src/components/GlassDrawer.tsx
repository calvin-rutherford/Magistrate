import React from 'react';
import { View, Text, TouchableOpacity, StyleSheet, Modal, ScrollView } from 'react-native';
import { GlassSurface } from './GlassSurface';
import { GlassTokens } from '../theme/glass';

interface GlassDrawerProps {
  visible: boolean;
  onClose: () => void;
  onNavigate: (route: string) => void;
  activeAgentsCount: number;
  attentionCount: number;
  prsCount: number;
}

export const GlassDrawer: React.FC<GlassDrawerProps> = ({
  visible,
  onClose,
  onNavigate,
  activeAgentsCount,
  attentionCount,
  prsCount
}) => {
  if (!visible) return null;

  return (
    <Modal visible={visible} animationType="fade" transparent onRequestClose={onClose}>
      <TouchableOpacity style={styles.overlay} activeOpacity={1} onPress={onClose}>
        <TouchableOpacity activeOpacity={1} style={styles.drawerContainer}>
          <GlassSurface variant="surface" intensity={65} style={styles.glassBody}>
            <View style={styles.header}>
              <View style={styles.headerTitleRow}>
                <View style={styles.emblemCircle}>
                  <View style={styles.innerEmblemDot} />
                </View>
                <Text style={styles.headerTitle}>MAGISTRATE</Text>
              </View>
              <TouchableOpacity onPress={onClose} style={styles.closeBtn}>
                <Text style={styles.closeBtnText}>✕</Text>
              </TouchableOpacity>
            </View>

            <ScrollView style={styles.menuList}>
              <Text style={styles.sectionHeading}>COMMAND NAVIGATION</Text>

              <TouchableOpacity style={styles.menuItem} onPress={() => { onNavigate('index'); onClose(); }}>
                <Text style={styles.menuItemText}>Situation Room</Text>
              </TouchableOpacity>

              <TouchableOpacity style={styles.menuItem} onPress={() => { onNavigate('agents'); onClose(); }}>
                <Text style={styles.menuItemText}>Running Agents</Text>
                <View style={[styles.badge, { backgroundColor: GlassTokens.colors.operational }]}>
                  <Text style={styles.badgeText}>{activeAgentsCount}</Text>
                </View>
              </TouchableOpacity>

              <TouchableOpacity style={styles.menuItem} onPress={() => { onNavigate('attention'); onClose(); }}>
                <Text style={styles.menuItemText}>Needs Attention</Text>
                {attentionCount > 0 && (
                  <View style={[styles.badge, { backgroundColor: GlassTokens.colors.blocked }]}>
                    <Text style={styles.badgeText}>{attentionCount}</Text>
                  </View>
                )}
              </TouchableOpacity>

              <TouchableOpacity style={styles.menuItem} onPress={() => { onNavigate('prs'); onClose(); }}>
                <Text style={styles.menuItemText}>Pull Requests</Text>
                <View style={[styles.badge, { backgroundColor: GlassTokens.colors.prs }]}>
                  <Text style={styles.badgeText}>{prsCount}</Text>
                </View>
              </TouchableOpacity>

              <TouchableOpacity style={styles.menuItem} onPress={() => { onNavigate('chat'); onClose(); }}>
                <Text style={styles.menuItemText}>Firstmate Chat</Text>
              </TouchableOpacity>

              <TouchableOpacity style={styles.menuItem} onPress={() => { onNavigate('account'); onClose(); }}>
                <Text style={styles.menuItemText}>Account & Settings</Text>
              </TouchableOpacity>
            </ScrollView>
          </GlassSurface>
        </TouchableOpacity>
      </TouchableOpacity>
    </Modal>
  );
};

const styles = StyleSheet.create({
  overlay: {
    flex: 1,
    backgroundColor: 'rgba(0, 0, 0, 0.65)',
    justifyContent: 'flex-start'
  },
  drawerContainer: {
    width: '80%',
    height: '100%'
  },
  glassBody: {
    flex: 1,
    padding: 20,
    borderTopRightRadius: 28,
    borderBottomRightRadius: 28
  },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 24,
    paddingTop: 20
  },
  headerTitleRow: {
    flexDirection: 'row',
    alignItems: 'center'
  },
  emblemCircle: {
    width: 24,
    height: 24,
    borderRadius: 12,
    backgroundColor: 'rgba(114, 245, 177, 0.2)',
    borderWidth: 1,
    borderColor: '#72F5B1',
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: 10
  },
  innerEmblemDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    backgroundColor: '#72F5B1'
  },
  headerTitle: {
    fontSize: 16,
    fontWeight: 'bold',
    color: GlassTokens.colors.textPrimary,
    letterSpacing: 2
  },
  closeBtn: {
    padding: 6
  },
  closeBtnText: {
    fontSize: 18,
    color: GlassTokens.colors.textSecondary
  },
  menuList: {
    flex: 1
  },
  sectionHeading: {
    fontSize: 11,
    fontWeight: 'bold',
    color: GlassTokens.colors.textMuted,
    letterSpacing: 1.5,
    marginBottom: 12
  },
  menuItem: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingVertical: 16,
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(255, 255, 255, 0.08)'
  },
  menuItemText: {
    fontSize: 15,
    color: GlassTokens.colors.textPrimary,
    fontWeight: '500'
  },
  badge: {
    borderRadius: 10,
    paddingHorizontal: 8,
    paddingVertical: 2
  },
  badgeText: {
    color: '#0D1322',
    fontWeight: 'bold',
    fontSize: 11
  }
});
