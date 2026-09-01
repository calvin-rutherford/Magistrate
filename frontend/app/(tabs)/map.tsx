import { StyleSheet, View, Text } from 'react-native';

/**
 * This route previously rendered a hand-written "government map" - branches,
 * states, and agencies that no upstream source ever returned. Nothing in the
 * active FastAPI product exposes that model, so the screen now states plainly
 * that the view is unavailable rather than presenting invented structure as
 * live data. The route is kept so existing deep links land somewhere honest.
 */
export default function GovernmentMapScreen() {
  return (
    <View style={styles.container} testID="government-map-unavailable">
      <Text style={styles.title}>Government Map</Text>
      <Text style={styles.body}>
        This view is unavailable. Magistrate has no live source for a
        branch/state/agency model, and this screen will not display an invented
        one.
      </Text>
      <Text style={styles.muted}>
        Live agent, attention, and pull request data is in the workspace shell.
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#050505',
    padding: 20,
    paddingTop: 60,
    gap: 12,
  },
  title: {
    fontSize: 28,
    fontWeight: '700',
    color: '#ffffff',
  },
  body: {
    fontSize: 14,
    lineHeight: 20,
    color: 'rgba(255, 255, 255, 0.78)',
  },
  muted: {
    fontSize: 12,
    lineHeight: 18,
    color: 'rgba(255, 255, 255, 0.5)',
  },
});
