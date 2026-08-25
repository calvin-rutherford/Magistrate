import React, { useEffect, useState } from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { GlassSurface } from './GlassSurface';
import { GlassTokens } from '../theme/glass';

interface WeatherData {
  label: string;
  temperature: number | null;
  location: string;
}

export const WeatherBadge: React.FC = () => {
  const [weather, setWeather] = useState<WeatherData>({
    label: 'Clear',
    temperature: 72,
    location: 'Local'
  });

  useEffect(() => {
    async function loadLiveWeather() {
      try {
        const url = 'https://api.open-meteo.com/v1/forecast?latitude=41.8781&longitude=-87.6298&current=temperature_2m,weather_code&temperature_unit=fahrenheit';
        const res = await fetch(url);
        if (res.ok) {
          const data = await res.json();
          const temp = Math.round(data.current.temperature_2m);
          const code = data.current.weather_code;
          let label = 'Clear';
          if (code > 0 && code <= 3) label = 'Cloudy';
          else if (code >= 51 && code <= 67) label = 'Rain';
          else if (code >= 71) label = 'Snow';
          setWeather({ label, temperature: temp, location: 'Chicago' });
        }
      } catch (e) {
        // Fallback
      }
    }
    loadLiveWeather();
  }, []);

  return (
    <View style={styles.container}>
      <GlassSurface variant="card" style={styles.pill}>
        <View style={styles.greenDot} />
        <Text style={styles.label}>{weather.label}</Text>
        {weather.temperature !== null && (
          <Text style={styles.temp}>{weather.temperature}°</Text>
        )}
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
  temp: {
    fontSize: 11,
    color: '#FFFFFF',
    fontWeight: 'bold'
  }
});
