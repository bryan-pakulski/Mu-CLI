// One Markdown rules-and-styles module (spec §5.2) for chat, interim turns
// and job documents (G§9.7): headings on the title scale, 24px list indents,
// blockquotes as a `glass-subtle` inset with italic ink-2 text and no
// coloured side rule (G§3.4), hairline tables with a sunken header, accent links,
// inline code on sunken with radius-xs, and fenced code through the Wash
// CodeBlock. Links only reach the OS through the http(s) allow-list.

import React, { useCallback, useMemo } from 'react';
import { StyleSheet, Text } from 'react-native';
import MarkdownRenderer from 'react-native-markdown-display';
import { openExternalUrl } from '../api/urlSafety';
import { useTheme } from '../theme/ThemeContext';
import { fonts, fontStyle, radii, spacing, typeStyle, type WashColors } from '../theme/tokens';
import { CodeBlock } from '../components/CodeBlock';
import { glassStyle, useGlass } from './Glass';

export type MarkdownVariant = 'chat' | 'compact' | 'document';

export function markdownStyles(colors: WashColors, variant: MarkdownVariant = 'chat', options: { solid?: boolean; strength?: number } = {}) {
  const bodyType = variant === 'chat' ? typeStyle('body-lg') : variant === 'compact' ? typeStyle('caption') : typeStyle('body');
  const mono = { fontFamily: fonts.mono, fontSize: typeStyle('mono').fontSize, lineHeight: typeStyle('mono').lineHeight };
  const heading = (name: 'title-2' | 'title-3' | 'body-lg') => ({ ...typeStyle(name), color: colors.plum, marginTop: spacing.md, marginBottom: spacing.sm });
  const compact = variant === 'compact';
  return StyleSheet.create({
    body: { ...bodyType, color: compact ? colors.ink2 : colors.ink, flexShrink: 1 },
    heading1: compact ? { ...typeStyle('label'), color: colors.ink2 } : heading('title-2'),
    heading2: compact ? { ...typeStyle('label'), color: colors.ink2 } : heading('title-3'),
    heading3: compact ? { ...typeStyle('label'), color: colors.ink2 } : { ...heading('body-lg'), ...fontStyle(fonts.ui, '600') },
    paragraph: { marginTop: 0, marginBottom: compact ? spacing.xs : spacing.sm, flexWrap: 'wrap', flexDirection: 'row' },
    bullet_list: { marginBottom: spacing.sm },
    ordered_list: { marginBottom: spacing.sm },
    list_item: { flexDirection: 'row', marginTop: 2, marginBottom: 2 },
    bullet_list_icon: { color: colors.ink3, marginLeft: spacing.xs, marginRight: spacing.sm, width: 12 },
    ordered_list_icon: { color: colors.ink3, marginRight: spacing.sm, minWidth: 16, fontVariant: ['tabular-nums'] },
    strong: fontStyle(fonts.ui, '600'),
    em: { fontStyle: 'italic' },
    link: { color: colors.accent.ink, textDecorationLine: 'underline' },
    // borderLeftWidth 0 overrides the renderer's default 4px grey side rule (G§3.4).
    blockquote: { ...glassStyle('subtle', colors, { solid: options.solid, strength: options.strength }), borderLeftWidth: 0, borderColor: 'transparent', borderRadius: radii.sm, paddingHorizontal: spacing.md, paddingVertical: spacing.sm, marginLeft: 0, marginVertical: spacing.sm },
    code_inline: { ...mono, color: colors.syntax.keyword, backgroundColor: colors.sunken, borderRadius: radii.xs, paddingHorizontal: spacing.xs },
    code_block: { ...mono, color: colors.ink, backgroundColor: colors.code, borderRadius: radii.sm, padding: spacing.base },
    fence: { ...mono, color: colors.ink, backgroundColor: colors.code, borderRadius: radii.sm, padding: spacing.base },
    hr: { backgroundColor: colors.hairline, height: 1, marginVertical: spacing.md },
    table: { borderColor: colors.hairline, borderWidth: 1, borderRadius: radii.sm, marginVertical: spacing.sm },
    thead: { backgroundColor: colors.sunken },
    th: { padding: spacing.sm, flex: 1, ...fontStyle(fonts.ui, '500'), color: colors.ink2 },
    td: { padding: spacing.sm, flex: 1 },
    tr: { borderBottomColor: colors.hairline, borderBottomWidth: 1, flexDirection: 'row' },
  });
}

/** Fenced and indented code render through the Wash CodeBlock; inline code is mono on sunken. */
export function markdownRules(colors: WashColors) {
  return {
    fence: (node: any) => (
      <CodeBlock key={node.key} code={node.content} language={(node.sourceInfo || '').trim()} colors={colors} />
    ),
    code_block: (node: any) => (
      <CodeBlock key={node.key} code={node.content} colors={colors} />
    ),
    code_inline: (node: any) => (
      <Text
        key={node.key}
        style={{
          color: colors.syntax.keyword,
          fontFamily: fonts.mono,
          fontSize: typeStyle('mono-sm').fontSize,
          backgroundColor: colors.sunken,
          borderRadius: radii.xs,
          paddingHorizontal: spacing.xs,
        }}
      >
        {node.content}
      </Text>
    ),
  };
}

const Renderer = React.memo(MarkdownRenderer);

export function Markdown({ children, variant = 'chat' }: { children: string; variant?: MarkdownVariant }) {
  const { colors } = useTheme();
  const { solid, strength } = useGlass();
  const style = useMemo(() => markdownStyles(colors, variant, { solid, strength }), [colors, variant, solid, strength]);
  const rules = useMemo(() => markdownRules(colors), [colors]);
  // Safe link handler: only http(s) URLs reach the OS.
  const onLinkPress = useCallback((url: string) => {
    void openExternalUrl(url);
    return false;
  }, []);
  return <Renderer style={style} rules={rules} onLinkPress={onLinkPress}>{children}</Renderer>;
}
