import React, { useEffect, useState } from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { GlassSurface } from './GlassSurface';
import { getWeather } from '../services/weather';

interface WeatherData {
  label: string;
}

export const WeatherBadge: React.FC = () => {
  const [weather, setWeather] = useState<WeatherData>({
    label: 'Clear',
  });

  useEffect(() => {
    async function loadLiveWeather() {
      const snapshot = await getWeather();
      setWeather({ label: snapshot.kind[0].toUpperCase() + snapshot.kind.slice(1) });
    }
    loadLiveWeather();
  }, []);

  return (
    <View style={styles.container}>
      <GlassSurface variant="card" style={styles.pill}>
        <View style={styles.greenDot} />
        <Text style={styles.label}>{weather.label}</Text>
      </GlassSurface>
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    alignItems: 'center',
    marginVertical: 4
  },
  pill: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 12,
    paddingVertical: 5,
    borderRadius: 20,
    gap: 6,
    backgroundColor: 'rgba(12, 29, 42, 0.35)'
  },
  greenDot: {
    width: 6,
    height: 6,
    borderRadius: 3,
    backgroundColor: '#72F5B1'
  },
  label: {
    fontSize: 11,
    color: 'rgba(255, 255, 255, 0.85)',
    fontWeight: '500'
  },
});
