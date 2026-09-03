import * as Clipboard from 'expo-clipboard';
import React, { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { MagiInlineNode, MagiResponseBlock, MagiResponseV1 } from '../services/MagiResponse';
import { openExternalUrl } from '../utils/externalLinks';

const colors = { cyan: '#24D8FF', violet: '#8B6CFF', muted: '#8E99AA', code: 'rgba(139,108,255,0.13)' };

type Props = {
  response: MagiResponseV1;
  color: string;
  mutedColor: string;
  dark: boolean;
  testID?: string;
};

function InlineContent({ nodes, color, mutedColor, headingLevel }: {
  nodes: MagiInlineNode[];
  color: string;
  mutedColor: string;
  headingLevel?: 1 | 2 | 3 | 4;
}) {
  return <Text selectable style={[
    styles.body,
    headingLevel === 1 ? styles.headingOne
      : headingLevel === 2 ? styles.headingTwo
        : headingLevel === 3 ? styles.headingThree
          : headingLevel === 4 ? styles.headingFour : undefined,
    { color },
  ]}>{nodes.map((node, index) => {
    const key = `${node.type}-${index}`;
    if (node.type === 'link') return <Text key={key} accessibilityRole="link" accessibilityLabel={`${node.text}, external link`} onPress={() => void openExternalUrl(node.url)} style={[styles.link, { color: colors.cyan }]}>{node.text}</Text>;
    if (node.type === 'inline_code') return <Text key={key} style={[styles.inlineCode, { color: mutedColor, backgroundColor: colors.code }]}>{node.text}</Text>;
    if (node.type === 'strong') return <Text key={key} style={styles.strong}>{node.text}</Text>;
    if (node.type === 'emphasis') return <Text key={key} style={styles.emphasis}>{node.text}</Text>;
    return <Text key={key}>{node.text}</Text>;
  })}</Text>;
}

function StructuredCodeBlock({ block, dark }: {
  block: Extract<MagiResponseBlock, { type: 'code' }>;
  dark: boolean;
}) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await Clipboard.setStringAsync(block.code);
    setCopied(true);
    setTimeout(() => setCopied(false), 1200);
  };
  return <View testID={`structured-block-${block.block_id}`} style={[styles.codeBlock, { backgroundColor: dark ? '#0B1018' : '#EEF1F6' }]}>
    <View style={styles.codeHeader}>
      <Text style={[styles.codeLanguage, { color: colors.muted }]}>{block.language || 'CODE'}</Text>
      <Pressable testID={`copy-structured-code-${block.block_id}`} accessibilityRole="button" accessibilityLabel="Copy code" onPress={() => void copy()} style={styles.copyButton}>
        <Text style={[styles.copyText, { color: colors.cyan }]}>{copied ? 'Copied' : 'Copy'}</Text>
      </Pressable>
    </View>
    <ScrollView horizontal nestedScrollEnabled showsHorizontalScrollIndicator contentContainerStyle={styles.codeScroll} style={styles.codeScroller as any}>
      <Text selectable style={[styles.codeText, { color: dark ? '#F4F5F7' : '#11151B' }]}>{block.code}</Text>
    </ScrollView>
  </View>;
}

function StructuredBlock({ block, color, mutedColor, dark }: {
  block: MagiResponseBlock;
  color: string;
  mutedColor: string;
  dark: boolean;
}) {
  if (block.type === 'code') return <StructuredCodeBlock block={block} dark={dark} />;
  if (block.type === 'heading') return <View testID={`structured-block-${block.block_id}`}><InlineContent nodes={block.content} color={color} mutedColor={mutedColor} headingLevel={block.level} /></View>;
  if (block.type === 'paragraph') return <View testID={`structured-block-${block.block_id}`}><InlineContent nodes={block.content} color={color} mutedColor={mutedColor} /></View>;
  if (block.type === 'quote') return <View testID={`structured-block-${block.block_id}`} style={[styles.quote, { borderLeftColor: colors.violet }]}><InlineContent nodes={block.content} color={color} mutedColor={mutedColor} /></View>;
  if (block.type === 'divider') return <View testID={`structured-block-${block.block_id}`} accessibilityElementsHidden style={[styles.divider, { backgroundColor: mutedColor }]} />;
  return <View testID={`structured-block-${block.block_id}`} style={styles.list}>{block.items.map((item, itemIndex) => <View key={itemIndex} style={styles.listRow}>
    <Text accessibilityElementsHidden style={[styles.bullet, { color: colors.violet }]}>{block.style === 'ordered' ? `${itemIndex + 1}.` : '•'}</Text>
    <View style={styles.listBody}><InlineContent nodes={item} color={color} mutedColor={mutedColor} /></View>
  </View>)}</View>;
}

/** Native rendering of an already validated closed document. */
export function StructuredAssistantMessage({ response, color, mutedColor, dark, testID }: Props) {
  // `actions` are reserved data in v1. Merely receiving model-authored action
  // objects never turns them into executable controls.
  return <View testID={testID} accessibilityLabel="Structured assistant response" style={styles.container}>
    {response.blocks.map(block => <StructuredBlock key={block.block_id} block={block} color={color} mutedColor={mutedColor} dark={dark} />)}
  </View>;
}

const styles = StyleSheet.create({
  container: { gap: 10 },
  body: { fontSize: 17, lineHeight: 26 },
  headingOne: { fontSize: 23, lineHeight: 30, fontWeight: '800' },
  headingTwo: { fontSize: 20, lineHeight: 28, fontWeight: '800' },
  headingThree: { fontSize: 18, lineHeight: 26, fontWeight: '800' },
  headingFour: { fontSize: 17, lineHeight: 25, fontWeight: '800' },
  strong: { fontWeight: '800' },
  emphasis: { fontStyle: 'italic' },
  link: { textDecorationLine: 'underline' },
  inlineCode: { fontFamily: 'monospace', fontSize: 14, lineHeight: 22, paddingHorizontal: 4, borderRadius: 4 },
  list: { gap: 7 },
  listRow: { flexDirection: 'row', alignItems: 'flex-start', gap: 9 },
  bullet: { width: 22, fontSize: 17, lineHeight: 26, textAlign: 'right' },
  listBody: { flex: 1, minWidth: 0 },
  quote: { borderLeftWidth: 3, paddingLeft: 12, opacity: 0.92 },
  divider: { height: StyleSheet.hairlineWidth, opacity: 0.35, marginVertical: 5 },
  codeBlock: { borderRadius: 11, overflow: 'hidden', marginVertical: 2 },
  codeHeader: { minHeight: 32, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 11, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: 'rgba(142,153,170,0.25)' },
  codeLanguage: { fontSize: 10, fontWeight: '800', letterSpacing: 0.8 },
  copyButton: { minWidth: 52, minHeight: 30, justifyContent: 'center', alignItems: 'flex-end' },
  copyText: { fontSize: 11, fontWeight: '800' },
  codeScroller: { touchAction: 'pan-x' } as any,
  codeScroll: { padding: 12, minWidth: '100%' },
  codeText: { fontFamily: 'monospace', fontSize: 13, lineHeight: 20 },
});
