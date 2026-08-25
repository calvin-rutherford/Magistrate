export type TimePeriod = 'dawn' | 'day' | 'dusk' | 'night';
export type WeatherSceneKey = 'dusk-mountain' | 'clear-day' | 'clear-night' | 'clouds' | 'rain' | 'storm' | 'sunset' | 'minimal-dark' | 'custom';

export interface EnvironmentTheme {
  timePeriod: TimePeriod;
  sceneKey: WeatherSceneKey;
  sceneImageUri: string;
  gradientColors: [string, string, string];
  glassOverlay: string;
  accentGlow: string;
  customUri?: string;
  dimOpacity: number;
}

const DUSK_MOUNTAIN_URI = 'https://images.unsplash.com/photo-1519681393784-d120267933ba?q=80&w=1600&auto=format&fit=crop';

export const SCENE_IMAGES: Record<WeatherSceneKey, string> = {
  'dusk-mountain': DUSK_MOUNTAIN_URI,
  'clear-day': 'https://images.unsplash.com/photo-1470071459604-3b5ec3a7fe05?q=80&w=1600&auto=format&fit=crop',
  'clear-night': 'https://images.unsplash.com/photo-1506744038136-46273834b3fb?q=80&w=1600&auto=format&fit=crop',
  'clouds': 'https://images.unsplash.com/photo-1534088568595-a066f410bcda?q=80&w=1600&auto=format&fit=crop',
  'rain': 'https://images.unsplash.com/photo-1515694346937-94d85e41e6f0?q=80&w=1600&auto=format&fit=crop',
  'storm': 'https://images.unsplash.com/photo-1511285560929-80b456fea0bc?q=80&w=1600&auto=format&fit=crop',
  'sunset': 'https://images.unsplash.com/photo-1495616811223-4d98c6e9c869?q=80&w=1600&auto=format&fit=crop',
  'minimal-dark': 'https://images.unsplash.com/photo-1550684848-fac1c5b4e853?q=80&w=1600&auto=format&fit=crop',
  'custom': DUSK_MOUNTAIN_URI
};

let activeSceneKey: WeatherSceneKey = 'dusk-mountain';
let customImageUri: string = '';

export function setActiveBackground(sceneKey: WeatherSceneKey, customUri?: string) {
  activeSceneKey = sceneKey;
  if (customUri) customImageUri = customUri;
}

export function getCurrentTimePeriod(date: Date = new Date()): TimePeriod {
  const hours = date.getHours();
  if (hours >= 5 && hours < 8) return 'dawn';
  if (hours >= 8 && hours < 17) return 'day';
  if (hours >= 17 && hours < 20) return 'dusk';
  return 'night';
}

export function getEnvironmentTheme(
  timePeriod: TimePeriod = getCurrentTimePeriod(),
  weatherScene: WeatherSceneKey = activeSceneKey
): EnvironmentTheme {
  const imageUri = (weatherScene === 'custom' && customImageUri) ? customImageUri : (SCENE_IMAGES[weatherScene] || DUSK_MOUNTAIN_URI);

  return {
    timePeriod,
    sceneKey: weatherScene,
    sceneImageUri: imageUri,
    gradientColors: ['rgba(30, 20, 45, 0.40)', 'rgba(15, 30, 50, 0.30)', 'rgba(10, 15, 26, 0.50)'],
    glassOverlay: 'rgba(15, 25, 40, 0.32)',
    accentGlow: '#34D399',
    customUri: customImageUri,
    dimOpacity: 0.22
  };
}
