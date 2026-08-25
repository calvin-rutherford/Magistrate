import React from 'react';
import { View, Text, TouchableOpacity, StyleSheet } from 'react-native';
import { GlassSurface } from './GlassSurface';

interface SummarySectionCardProps {
  title: string;
  countText?: string;
  itemTitle: string;
  itemSubtitle: string;
  statusBadge?: string;
  onPress: () => void;
}

export const SummarySectionCard: React.FC<SummarySectionCardProps> = ({
  title,
  countText,
  itemTitle,
  itemSubtitle,
  statusBadge,
  onPress
}) => {
  return (
    <TouchableOpacity onPress={onPress} activeOpacity={0.85} style={styles.container}>
      <GlassSurface variant="card" style={styles.card}>
        <View style={styles.headerRow}>
          <View style={styles.titleRow}>
            <View style={styles.accentBar} />
            <Text style={styles.sectionTitle}>{title}</Text>
            {countText ? (
              <View style={styles.countBadge}>
                <Text style={styles.countText}>{countText}</Text>
              </View>
            ) : null}
          </View>
          <Text style={styles.chevron}>›</Text>
        </View>

        <View style={styles.bodyContent}>
          <View style={styles.textCol}>
            <Text style={styles.itemTitle}>{itemTitle}</Text>
            <Text style={styles.itemSub} numberOfLines={1}>{itemSubtitle}</Text>
          </View>
          {statusBadge ? (
            <View style={styles.badgePill}>
              <Text style={styles.badgeText}>{statusBadge}</Text>
            </View>
          ) : null}
        </View>
      </GlassSurface>
    </TouchableOpacity>
  );
};

const styles = StyleSheet.create({
  container: {
    marginVertical: 4
  },
  card: {
    padding: 15,
    borderRadius: 16,
    backgroundColor: 'rgba(12, 22, 34, 0.32)',
    borderColor: 'rgba(255, 255, 255, 0.14)'
  },
  headerRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 8
  },
  titleRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8
  },
  accentBar: {
    width: 2.5,
    height: 12,
    borderRadius: 1.5,
    backgroundColor: 'rgba(255, 255, 255, 0.45)'
  },
  sectionTitle: {
    fontFamily: 'monospace',
    fontSize: 10,
    fontWeight: 'bold',
    color: 'rgba(255, 255, 255, 0.55)',
    letterSpacing: 1.2
  },
  countBadge: {
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 8,
    backgroundColor: 'rgba(255, 255, 255, 0.08)',
    borderWidth: 1,
    borderColor: 'rgba(255, 255, 255, 0.15)'
  },
  countText: {
    fontFamily: 'monospace',
    fontSize: 9.5,
    fontWeight: 'bold',
    color: 'rgba(255, 255, 255, 0.75)'
  },
  chevron: {
    fontSize: 16,
    fontWeight: 'bold',
    color: 'rgba(255, 255, 255, 0.35)'
  },
  bodyContent: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center'
  },
  textCol: {
    flex: 1,
    paddingRight: 10
  },
  itemTitle: {
    fontSize: 13.5,
    fontWeight: 'bold',
    color: 'rgba(255, 255, 255, 0.92)'
  },
  itemSub: {
    fontSize: 11.5,
    color: 'rgba(255, 255, 255, 0.50)',
    marginTop: 2
  },
  badgePill: {
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: 'rgba(255, 255, 255, 0.22)',
    backgroundColor: 'rgba(255, 255, 255, 0.05)'
  },
  badgeText: {
    fontFamily: 'monospace',
    fontSize: 9.5,
    fontWeight: 'bold',
    color: 'rgba(255, 255, 255, 0.80)',
    letterSpacing: 0.5
  }
});
