import { ImageSourcePropType } from 'react-native';

export type TimePeriod = 'dawn' | 'day' | 'dusk' | 'night';
export type WeatherKind = 'clear' | 'cloudy' | 'rain' | 'snow' | 'storm';
export type WeatherSceneKey = 'auto' | 'dusk-mountain' | 'clear-day' | 'clear-night' | 'clouds' | 'rain' | 'storm' | 'sunset' | 'minimal-dark' | 'custom';
export interface EnvironmentTheme { timePeriod: TimePeriod; sceneKey: WeatherSceneKey; sceneImage: ImageSourcePropType; weather: WeatherKind; customUri?: string; dimOpacity: number }

export const TIME_IMAGES: Record<TimePeriod, ImageSourcePropType> = {
  dawn: require('../../assets/images/environment/dawn.png'),
  day: require('../../assets/images/environment/day.png'),
  dusk: require('../../assets/images/environment/dusk.png'),
  night: require('../../assets/images/environment/night.png'),
};

let activeSceneKey: WeatherSceneKey = 'auto';
let customImageUri = '';
const backgroundListeners = new Set<() => void>();

export function subscribeActiveBackground(listener: () => void): () => void {
  backgroundListeners.add(listener);
  return () => backgroundListeners.delete(listener);
}
export function setActiveBackground(sceneKey: WeatherSceneKey, customUri?: string) {
  activeSceneKey = sceneKey;
  if (sceneKey === 'custom' && customUri) customImageUri = customUri;
  if (sceneKey !== 'custom') customImageUri = '';
  backgroundListeners.forEach(listener => listener());
}
export function getCurrentTimePeriod(date: Date = new Date()): TimePeriod {
  const hours = date.getHours();
  if (hours >= 5 && hours < 8) return 'dawn';
  if (hours >= 8 && hours < 17) return 'day';
  if (hours >= 17 && hours < 20) return 'dusk';
  return 'night';
}
export function weatherCodeToKind(code: number): WeatherKind {
  if ([95, 96, 99].includes(code)) return 'storm';
  if ([71, 73, 75, 77, 85, 86].includes(code)) return 'snow';
  if ([51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82].includes(code)) return 'rain';
  if ([1, 2, 3, 45, 48].includes(code)) return 'cloudy';
  return 'clear';
}
function selectedWeather(fetched: WeatherKind): WeatherKind {
  if (['dusk-mountain', 'clear-day', 'clear-night', 'sunset', 'minimal-dark'].includes(activeSceneKey)) return 'clear';
  if (activeSceneKey === 'clouds') return 'cloudy';
  if (activeSceneKey === 'rain') return 'rain';
  if (activeSceneKey === 'storm') return 'storm';
  return fetched;
}
export function getEnvironmentTheme(weather: WeatherKind = 'clear', date: Date = new Date()): EnvironmentTheme {
  const selectedPeriod: Partial<Record<WeatherSceneKey, TimePeriod>> = {
    'dusk-mountain': 'dusk', 'clear-day': 'day', 'clear-night': 'night', sunset: 'dusk', 'minimal-dark': 'night',
  };
  const timePeriod = selectedPeriod[activeSceneKey] || getCurrentTimePeriod(date);
  const isCustom = activeSceneKey === 'custom' && !!customImageUri;
  return { timePeriod, sceneKey: activeSceneKey, sceneImage: isCustom ? { uri: customImageUri } : TIME_IMAGES[timePeriod], weather: selectedWeather(weather), customUri: customImageUri || undefined, dimOpacity: activeSceneKey === 'minimal-dark' ? 0.72 : timePeriod === 'day' ? 0.48 : 0.34 };
}
