import React, { useEffect, useState, useRef } from 'react';
import { View, Text, StyleSheet, ScrollView, TouchableOpacity, TextInput, NativeSyntheticEvent, NativeScrollEvent } from 'react-native';
import { EnvironmentBackground } from '../../src/components/EnvironmentBackground';
import { GlassSurface } from '../../src/components/GlassSurface';
import { GlassDrawer } from '../../src/components/GlassDrawer';
import { TerminusControlBar } from '../../src/components/TerminusControlBar';
import { fetchCaptainOutput, sendCaptainPrompt, sendAgentKey } from '../../src/api/client';
import { useRouter } from 'expo-router';

export default function ChatScreen() {
  const router = useRouter();

  const [output, setOutput] = useState<string>('');
  const [promptText, setPromptText] = useState<string>('');
  const [showDrawer, setShowDrawer] = useState<boolean>(false);
  const [isScrolledUp, setIsScrolledUp] = useState<boolean>(false);
  const [hasNewMessages, setHasNewMessages] = useState<boolean>(false);
  const [isThinking, setIsThinking] = useState<boolean>(false);

  const scrollRef = useRef<ScrollView>(null);
  const inputRef = useRef<TextInput>(null);

  const loadOutput = async () => {
    try {
      const data = await fetchCaptainOutput(25);
      const newText = data?.output || 'No output.';
      setOutput(prev => {
        if (prev !== newText) {
          if (isScrolledUp) setHasNewMessages(true);
        }
        return newText;
      });
    } catch (e) {
      console.error('Chat output load error:', e);
    }
  };

  useEffect(() => {
    loadOutput();
    const interval = setInterval(loadOutput, 2000);
    return () => clearInterval(interval);
  }, [isScrolledUp]);

  useEffect(() => {
    if (!isScrolledUp) {
      scrollRef.current?.scrollToEnd({ animated: true });
    }
  }, [output]);

  const handleScroll = (event: NativeSyntheticEvent<NativeScrollEvent>) => {
    const { contentOffset, layoutMeasurement, contentSize } = event.nativeEvent;
    const isAtBottom = contentOffset.y + layoutMeasurement.height >= contentSize.height - 35;
    if (isAtBottom) {
      setIsScrolledUp(false);
      setHasNewMessages(false);
    } else {
      setIsScrolledUp(true);
    }
  };

  const scrollToBottom = () => {
    scrollRef.current?.scrollToEnd({ animated: true });
    setIsScrolledUp(false);
    setHasNewMessages(false);
  };

  const handleSend = async () => {
    const text = promptText.trim();
    if (!text) {
      await sendAgentKey('captain', 'Enter');
      loadOutput();
      return;
    }

    setPromptText('');
    setIsThinking(true);
    try {
      await sendCaptainPrompt(text, 'iphone', 'captain');
      setTimeout(() => {
        loadOutput();
        setIsThinking(false);
        scrollToBottom();
      }, 600);
    } catch (e) {
      console.error('Send prompt error:', e);
      setIsThinking(false);
    }
  };

  const handleNavigate = (route: string) => {
    if (route === 'chat') return;
    router.push('/' + route as any);
  };

  return (
    <EnvironmentBackground>
      <View style={styles.headerRow}>
        <TouchableOpacity onPress={() => router.back()}>
          <GlassSurface variant="control" style={styles.headerCircleBtn}>
            <Text style={styles.backText}>←</Text>
          </GlassSurface>
        </TouchableOpacity>

        <Text style={styles.headerTitle}>FIRSTMATE CHAT</Text>

        <TouchableOpacity onPress={() => setShowDrawer(true)}>
          <GlassSurface variant="control" style={styles.headerCircleBtn}>
            <Text style={styles.backText}>≡</Text>
          </GlassSurface>
        </TouchableOpacity>
      </View>

      <View style={styles.chatContainer}>
        <GlassSurface variant="surface" intensity={60} style={styles.terminalGlassBox}>
          <View style={styles.terminalHeaderRow}>
            <View style={[styles.statusDot, isThinking ? styles.dotThinking : undefined]} />
            <Text style={styles.terminalTitle}>
              {'<firstmate> <melkezic/firstmate>'}
            </Text>
          </View>

          <View style={styles.terminalScrollContainer}>
            <ScrollView
              ref={scrollRef}
              style={styles.terminalScroll}
              onScroll={handleScroll}
              scrollEventThrottle={16}
            >
              <Text style={styles.terminalText}>{output}</Text>
            </ScrollView>

            {(hasNewMessages || isScrolledUp) ? (
              <TouchableOpacity style={styles.scrollBadgeBtn} onPress={scrollToBottom} activeOpacity={0.8}>
                <GlassSurface variant="control" style={styles.scrollBadgeSurface}>
                  <Text style={styles.scrollBadgeText}>
                    {hasNewMessages ? '↓ NEW MESSAGES (TAP TO SCROLL)' : '↓ SCROLL TO BOTTOM'}
                  </Text>
                </GlassSurface>
              </TouchableOpacity>
            ) : null}
          </View>
        </GlassSurface>

        <TerminusControlBar target="captain" onKeySent={() => setTimeout(loadOutput, 400)} />

        <View style={styles.inputComposerRow}>
          <TextInput
            ref={inputRef}
            style={styles.textInput}
            placeholder="Command Codex Captain..."
            placeholderTextColor="rgba(255, 255, 255, 0.45)"
            value={promptText}
            onChangeText={setPromptText}
            onSubmitEditing={() => handleSend()}
          />

          <TouchableOpacity style={styles.sendBtn} onPress={() => handleSend()}>
            <Text style={styles.sendBtnText}>SEND ↵</Text>
          </TouchableOpacity>
        </View>
      </View>

      <GlassDrawer
        visible={showDrawer}
        onClose={() => setShowDrawer(false)}
        onNavigate={handleNavigate}
        activeAgentsCount={1}
        attentionCount={0}
        prsCount={2}
      />
    </EnvironmentBackground>
  );
}

const styles = StyleSheet.create({
  headerRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingHorizontal: 16, paddingTop: 12, marginBottom: 6 },
  headerTitle: { fontFamily: 'monospace', fontSize: 14, fontWeight: 'bold', color: '#FFFFFF', letterSpacing: 2 },
  headerCircleBtn: { width: 36, height: 36, borderRadius: 18, justifyContent: 'center', alignItems: 'center' },
  backText: { color: '#FFFFFF', fontSize: 16, fontWeight: 'bold' },
  chatContainer: { flex: 1, paddingHorizontal: 16, paddingBottom: 16 },
  terminalGlassBox: { height: 480, padding: 14, borderRadius: 20, marginBottom: 8 },
  terminalHeaderRow: { flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 8 },
  statusDot: { width: 8, height: 8, borderRadius: 4, backgroundColor: '#FFFFFF' },
  dotThinking: { backgroundColor: '#AAAAAA' },
  terminalTitle: { fontFamily: 'monospace', fontSize: 11, fontWeight: 'bold', color: '#FFFFFF', letterSpacing: 1.2 },
  terminalScrollContainer: { flex: 1, position: 'relative' },
  terminalScroll: { flex: 1, backgroundColor: '#000000', borderRadius: 12, borderWidth: 1, borderColor: 'rgba(255, 255, 255, 0.3)', padding: 12 },
  terminalText: { fontFamily: 'monospace', fontSize: 12, color: '#FFFFFF', lineHeight: 18 },
  scrollBadgeBtn: { position: 'absolute', bottom: 10, alignSelf: 'center', zIndex: 10 },
  scrollBadgeSurface: { paddingHorizontal: 14, paddingVertical: 6, borderRadius: 16, backgroundColor: 'rgba(255, 255, 255, 0.2)', borderColor: '#FFFFFF', borderWidth: 1 },
  scrollBadgeText: { fontFamily: 'monospace', color: '#000000', fontWeight: 'bold', fontSize: 10, letterSpacing: 0.8 },
  inputComposerRow: { flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 4 },
  textInput: { flex: 1, backgroundColor: 'rgba(255, 255, 255, 0.08)', borderRadius: 12, borderWidth: 1, borderColor: 'rgba(255, 255, 255, 0.2)', paddingHorizontal: 12, paddingVertical: 10, color: '#FFFFFF', fontSize: 14 },
  sendBtn: { backgroundColor: '#FFFFFF', borderRadius: 12, paddingVertical: 11, paddingHorizontal: 14 },
  sendBtnText: { fontFamily: 'monospace', color: '#000000', fontWeight: 'bold', fontSize: 12 }
});
