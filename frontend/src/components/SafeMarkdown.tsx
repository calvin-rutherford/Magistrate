import * as Clipboard from 'expo-clipboard';
import React, { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { openExternalUrl } from '../utils/externalLinks';
import { parseSafeMarkdown, SafeInline, SafeMarkdownBlock } from '../services/ChatFormatting';

const colors = { cyan: '#24D8FF', violet: '#8B6CFF', muted: '#8E99AA', code: 'rgba(139,108,255,0.13)' };

type Props = { markdown: string; color: string; mutedColor: string; dark: boolean; testID?: string };

function InlineText({ nodes, color, mutedColor, heading = false, level = 1 }: { nodes: SafeInline[]; color: string; mutedColor: string; heading?: boolean; level?: 1 | 2 | 3 }) {
  return <Text selectable style={[styles.body, heading ? (level === 1 ? styles.headingOne : level === 2 ? styles.headingTwo : styles.headingThree) : undefined, { color }]}>{nodes.map((node, index) => {
    if (node.type === 'link') return <Text key={index} accessibilityRole="link" accessibilityLabel={`${node.value}, external link`} onPress={() => void openExternalUrl(node.url)} style={[styles.link, { color: colors.cyan }]}>{node.value}</Text>;
    if (node.type === 'code') return <Text key={index} style={[styles.inlineCode, { color: mutedColor, backgroundColor: colors.code }]}>{node.value}</Text>;
    if (node.type === 'strong') return <Text key={index} style={styles.strong}>{node.value}</Text>;
    if (node.type === 'emphasis') return <Text key={index} style={styles.emphasis}>{node.value}</Text>;
    return <Text key={index}>{node.value}</Text>;
  })}</Text>;
}

function CodeBlock({ block, dark }: { block: Extract<SafeMarkdownBlock, { type: 'code' }>; dark: boolean }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => { await Clipboard.setStringAsync(block.value); setCopied(true); setTimeout(() => setCopied(false), 1200); };
  return <View testID="assistant-code-block" style={[styles.codeBlock, { backgroundColor: dark ? '#0B1018' : '#EEF1F6' }]}>
    <View style={styles.codeHeader}><Text style={[styles.codeLanguage, { color: colors.muted }]}>{block.language || 'CODE'}</Text><Pressable testID="copy-code" accessibilityRole="button" accessibilityLabel="Copy code" onPress={() => void copy()} style={styles.copyButton}><Text style={[styles.copyText, { color: colors.cyan }]}>{copied ? 'Copied' : 'Copy'}</Text></Pressable></View>
    <ScrollView horizontal nestedScrollEnabled showsHorizontalScrollIndicator contentContainerStyle={styles.codeScroll} style={styles.codeScroller as any}><Text selectable style={[styles.codeText, { color: dark ? '#F4F5F7' : '#11151B' }]}>{block.value}</Text></ScrollView>
  </View>;
}

export function SafeMarkdown({ markdown, color, mutedColor, dark, testID }: Props) {
  const blocks = parseSafeMarkdown(markdown);
  return <View testID={testID} accessibilityLabel="Assistant response" style={styles.container}>{blocks.map((block, index) => {
    if (block.type === 'code') return <CodeBlock key={index} block={block} dark={dark} />;
    if (block.type === 'heading' || block.type === 'paragraph') { const textBlock = block as Extract<SafeMarkdownBlock, { type: 'heading' | 'paragraph' }>; return <InlineText key={index} nodes={textBlock.inline} color={color} mutedColor={mutedColor} heading={textBlock.type === 'heading'} level={textBlock.level} />; }
    if (block.type === 'unordered-list' || block.type === 'ordered-list') return <View key={index} style={styles.list}>{block.items.map((item, itemIndex) => <View key={itemIndex} style={styles.listRow}><Text accessibilityElementsHidden style={[styles.bullet, { color: colors.violet }]}>{block.type === 'ordered-list' ? `${itemIndex + 1}.` : '•'}</Text><View style={styles.listBody}><InlineText nodes={item} color={color} mutedColor={mutedColor} /></View></View>)}</View>;
    const paragraph = block as Extract<SafeMarkdownBlock, { type: 'paragraph' | 'heading' }>;
    return <InlineText key={index} nodes={paragraph.inline} color={color} mutedColor={mutedColor} />;
  })}</View>;
}

const styles = StyleSheet.create({
  container: { gap: 10 },
  body: { fontSize: 17, lineHeight: 26 },
  headingOne: { fontSize: 23, lineHeight: 30, fontWeight: '800' },
  headingTwo: { fontSize: 20, lineHeight: 28, fontWeight: '800' },
  headingThree: { fontSize: 18, lineHeight: 26, fontWeight: '800' },
  strong: { fontWeight: '800' },
  emphasis: { fontStyle: 'italic' },
  link: { textDecorationLine: 'underline' },
  inlineCode: { fontFamily: 'monospace', fontSize: 14, lineHeight: 22, paddingHorizontal: 4, borderRadius: 4 },
  list: { gap: 7 },
  listRow: { flexDirection: 'row', alignItems: 'flex-start', gap: 9 },
  bullet: { width: 22, fontSize: 17, lineHeight: 26, textAlign: 'right' },
  listBody: { flex: 1, minWidth: 0 },
  codeBlock: { borderRadius: 11, overflow: 'hidden', marginVertical: 2 },
  codeHeader: { minHeight: 32, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 11, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: 'rgba(142,153,170,0.25)' },
  codeLanguage: { fontSize: 10, fontWeight: '800', letterSpacing: 0.8 },
  copyButton: { minWidth: 52, minHeight: 30, justifyContent: 'center', alignItems: 'flex-end' },
  copyText: { fontSize: 11, fontWeight: '800' },
  codeScroller: { touchAction: 'pan-x' } as any,
  codeScroll: { padding: 12, minWidth: '100%' },
  codeText: { fontFamily: 'monospace', fontSize: 13, lineHeight: 20 },
});
