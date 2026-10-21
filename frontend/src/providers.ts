// The six subscriptions (keys mirror src/whattowatch/providers.py).
export interface ProviderInfo {
  key: string
  name: string
  color: string
}

export const PROVIDERS: ProviderInfo[] = [
  { key: 'netflix', name: 'Netflix', color: '#e50914' },
  { key: 'disney_plus', name: 'Disney+', color: '#113ccf' },
  { key: 'hbo_max', name: 'HBO Max', color: '#5822b4' },
  { key: 'prime_video', name: 'Prime Video', color: '#00a8e1' },
  { key: 'apple_tv_plus', name: 'Apple TV+', color: '#3a3a3c' },
  { key: 'skyshowtime', name: 'SkyShowtime', color: '#0b6e4f' },
]

export const PROVIDER_BY_KEY: Record<string, ProviderInfo> = Object.fromEntries(
  PROVIDERS.map((p) => [p.key, p]),
)
