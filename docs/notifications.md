# Attention notifications

The root layout polls the gateway's deduplicated `/notifications/events` transition feed. Each new actionable attention item is delivered through the strongest supported client channel:

- Web: the browser `Notification` API, when supported and permission is granted.
- iOS/Android: `expo-notifications` local notification scheduling, when device permission is granted.
- Otherwise: the in-app notification stack, with per-item open and dismiss actions.

The web channel is not a service worker or server push subscription: it requires the Magistrate page to be open, and browsers may reject permission requests made outside a user gesture. Native background delivery likewise depends on the platform's notification/background execution policy. In either limitation case, the app keeps the item visible in its in-app fallback rather than claiming that a system notification was delivered.
