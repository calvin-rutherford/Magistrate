import { weatherCodeToKind, WeatherKind } from './environmentTheme';
export interface WeatherSnapshot { kind: WeatherKind; fetchedAt: number }
const REFRESH_MS = 30 * 60 * 1000;
const STORAGE_KEY = 'magistrate:ambient-weather:v1';
let memoryCache: WeatherSnapshot | null = null;
function configuredCoordinates() {
  const latitude = Number(process.env.EXPO_PUBLIC_WEATHER_LATITUDE);
  const longitude = Number(process.env.EXPO_PUBLIC_WEATHER_LONGITUDE);
  if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return null;
  return { latitude: Math.round(latitude * 100) / 100, longitude: Math.round(longitude * 100) / 100 };
}
function readCache(): WeatherSnapshot | null {
  if (memoryCache) return memoryCache;
  try { const raw = typeof localStorage === 'undefined' ? null : localStorage.getItem(STORAGE_KEY); memoryCache = raw ? JSON.parse(raw) : null; } catch { memoryCache = null; }
  return memoryCache;
}
function writeCache(value: WeatherSnapshot) {
  memoryCache = value;
  try { if (typeof localStorage !== 'undefined') localStorage.setItem(STORAGE_KEY, JSON.stringify(value)); } catch {}
}
export async function getWeather(force = false): Promise<WeatherSnapshot> {
  const cached = readCache();
  const now = Date.now();
  if (!force && cached && now - cached.fetchedAt < REFRESH_MS) return cached;
  const coords = configuredCoordinates();
  if (!coords || process.env.EXPO_PUBLIC_AMBIENT_DATA_SAVER === 'true') return cached ?? { kind: 'clear', fetchedAt: 0 };
  try {
    const query = `latitude=${coords.latitude}&longitude=${coords.longitude}&current=weather_code`;
    const response = await fetch(`https://api.open-meteo.com/v1/forecast?${query}`, { headers: { Accept: 'application/json' } });
    if (!response.ok) throw new Error(`weather ${response.status}`);
    const payload = await response.json();
    const snapshot = { kind: weatherCodeToKind(Number(payload.current?.weather_code)), fetchedAt: now };
    writeCache(snapshot); return snapshot;
  } catch { return cached ?? { kind: 'clear', fetchedAt: 0 }; }
}
export const WEATHER_REFRESH_MS = REFRESH_MS;
