import React from 'react';
import { View, Text, TouchableOpacity, StyleSheet } from 'react-native';

interface Props {
  children: React.ReactNode;
}

interface State {
  hasError: boolean;
  error?: Error;
}

export class ErrorBoundary extends React.Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    console.error('Magistrate UI Error:', error, errorInfo);
  }

  handleReload = () => {
    this.setState({ hasError: false });
    if (typeof window !== 'undefined') {
      window.location.reload();
    }
  };

  render() {
    if (this.state.hasError) {
      return (
        <View style={styles.container}>
          <Text style={styles.title}>MAGISTRATE HUD RECOVERY</Text>
          <Text style={styles.subtitle}>
            A temporary render glitch occurred. Tap below to reload.
          </Text>
          {this.state.error?.message && (
            <Text style={styles.errorBox}>{this.state.error.message}</Text>
          )}
          <TouchableOpacity style={styles.btn} onPress={this.handleReload}>
            <Text style={styles.btnText}>RELOAD COCKPIT ↺</Text>
          </TouchableOpacity>
        </View>
      );
    }

    return this.props.children;
  }
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#0D1322',
    justifyContent: 'center',
    alignItems: 'center',
    padding: 24
  },
  title: {
    color: '#72F5B1',
    fontWeight: 'bold',
    fontSize: 18,
    letterSpacing: 2,
    marginBottom: 10
  },
  subtitle: {
    color: 'rgba(255, 255, 255, 0.75)',
    fontSize: 14,
    textAlign: 'center',
    marginBottom: 20
  },
  errorBox: {
    color: '#FFAA20',
    fontFamily: 'monospace',
    fontSize: 12,
    backgroundColor: 'rgba(0, 0, 0, 0.5)',
    padding: 12,
    borderRadius: 8,
    marginBottom: 20,
    maxWidth: '90%'
  },
  btn: {
    backgroundColor: '#72F5B1',
    paddingHorizontal: 20,
    paddingVertical: 12,
    borderRadius: 12
  },
  btnText: {
    color: '#0D1322',
    fontWeight: 'bold',
    fontSize: 14
  }
});
