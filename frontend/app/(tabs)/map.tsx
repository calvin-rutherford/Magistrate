import { StyleSheet, View, Text, ScrollView } from 'react-native';

export default function GovernmentMapScreen() {
  return (
    <View style={styles.container}>
      <Text style={styles.title}>Government Map</Text>
      <ScrollView style={styles.content}>
        
        {/* Branches */}
        <View style={styles.card}>
          <Text style={styles.cardTitle}>Federal Branches</Text>
          <Text style={styles.node}>🏛️ Executive Office</Text>
          <Text style={styles.node}>⚖️ Judicial Court System</Text>
          <Text style={styles.node}>📜 Legislative Assembly</Text>
        </View>

        {/* States / Projects */}
        <View style={styles.card}>
          <Text style={styles.cardTitle}>States (Projects)</Text>
          <Text style={styles.stateTitle}>🌎 Magistrate State</Text>
          <View style={styles.indent}>
            <Text style={styles.node}>🏙️ Core Repository (City)</Text>
            <Text style={styles.node}>🏙️ React Native App (City)</Text>
          </View>
        </View>

        <View style={styles.card}>
          <Text style={styles.cardTitle}>Active Agencies</Text>
          <Text style={styles.node}>⚙️ Backend Engineering Agency</Text>
          <Text style={styles.node}>🎨 Frontend Engineering Agency</Text>
          <Text style={styles.node}>🛡️ Threat Modeling Agency</Text>
        </View>

      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#050505',
    padding: 20,
    paddingTop: 60,
  },
  title: {
    fontSize: 28,
    fontWeight: '700',
    color: '#ffffff',
    marginBottom: 20,
    fontFamily: 'System',
    letterSpacing: 1.5,
  },
  content: {
    flex: 1,
  },
  card: {
    backgroundColor: 'rgba(255,255,255,0.05)',
    borderRadius: 16,
    padding: 20,
    marginBottom: 16,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.1)',
  },
  cardTitle: {
    fontSize: 16,
    fontWeight: '600',
    color: '#a0a0a0',
    marginBottom: 12,
    textTransform: 'uppercase',
    letterSpacing: 1.2,
  },
  stateTitle: {
    color: '#ffffff',
    fontSize: 18,
    fontWeight: 'bold',
    marginBottom: 8,
  },
  node: {
    color: '#dddddd',
    fontSize: 16,
    marginBottom: 8,
    paddingVertical: 4,
  },
  indent: {
    paddingLeft: 20,
    borderLeftWidth: 1,
    borderColor: 'rgba(255,255,255,0.2)',
    marginLeft: 8,
  }
});
