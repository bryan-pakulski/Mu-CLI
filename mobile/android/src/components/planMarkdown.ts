import { StyleSheet } from 'react-native';
import type { ThemeColors } from '../theme/tokens';
import { fontSizes, fontWeights, fonts, radii, spacing, tracking, typography } from '../theme/tokens';

export type PlanRow = { key: string; text: string };
export type PlanSection = 'Acceptance criteria' | 'Checks';
export const PLAN_SECTIONS: PlanSection[] = ['Acceptance criteria', 'Checks'];

export function sectionPattern(title: PlanSection) {
  return new RegExp(`(^## ${title}[^\\S\\n]*\\n)([\\s\\S]*?)(?=^## |$(?![\\s\\S]))`, 'im');
}

export function rowsFrom(markdown: string, title: PlanSection): PlanRow[] {
  const body = sectionPattern(title).exec(markdown)?.[2] || '';
  return body.split('\n').filter(line => line.trim()).map((line, index) => {
    const match = title === 'Checks'
      ? /^\s*[-*]\s*suite:\s*(\S*)\s+cmd: ?(.*)$/.exec(line)
      : /^\s*[-*]\s*\[([^\]]*)\] ?(.*)$/.exec(line);
    return match ? { key: match[1], text: match[2] } : {
      key: title === 'Checks' ? `check${index + 1}` : `AC-${index + 1}`,
      text: line.replace(/^\s*(?:[-*]|\d+[.)])\s+/, ''),
    };
  });
}

export function replaceRows(markdown: string, title: PlanSection, rows: PlanRow[]) {
  const body = rows.map(row => title === 'Checks'
    ? `- suite: ${row.key}   cmd: ${row.text}` : `- [${row.key}] ${row.text}`).join('\n');
  const section = `## ${title}\n${body}\n\n`;
  return sectionPattern(title).test(markdown)
    ? markdown.replace(sectionPattern(title), () => section)
    : `${markdown.trimEnd()}\n\n${section}`;
}

/** Plan prose without front matter or the structured sections shown as cards. */
export function planProse(markdown: string): string {
  let text = markdown.replace(/^---\n[\s\S]*?\n---\n?/, '');
  for (const title of PLAN_SECTIONS) text = text.replace(sectionPattern(title), '');
  return text.trim();
}

/** Readable, wrap-first Markdown styles shared by job screens. */
export function planMarkdownStyles(colors: ThemeColors) {
  const mono = { fontFamily: fonts.mono, fontSize: fontSizes.sm, lineHeight: typography.sm.lineHeight };
  return StyleSheet.create({
    body: { color: colors.ink, fontSize: fontSizes.base, lineHeight: typography.base.lineHeight, flexShrink: 1 },
    heading1: { color: colors.ink, fontSize: fontSizes.lg, lineHeight: typography.lg.lineHeight, fontWeight: fontWeights.semibold, letterSpacing: tracking.tight, marginTop: spacing.sm, marginBottom: spacing.sm },
    heading2: { color: colors.ink, fontSize: fontSizes.md, lineHeight: typography.md.lineHeight, fontWeight: fontWeights.semibold, marginTop: spacing.base, marginBottom: spacing.xs },
    heading3: { color: colors.ink, fontSize: fontSizes.base, lineHeight: typography.base.lineHeight, fontWeight: fontWeights.semibold, marginTop: spacing.md },
    paragraph: { marginTop: spacing.xs, marginBottom: spacing.sm, flexWrap: 'wrap', flexDirection: 'row' },
    bullet_list: { marginBottom: spacing.sm }, ordered_list: { marginBottom: spacing.sm },
    list_item: { flexDirection: 'row', marginBottom: spacing.xs },
    bullet_list_icon: { color: colors.ink3, marginRight: spacing.sm, lineHeight: typography.md.lineHeight },
    ordered_list_icon: { color: colors.ink3, marginRight: spacing.sm, lineHeight: typography.md.lineHeight, fontVariant: ['tabular-nums'] },
    strong: { fontWeight: fontWeights.semibold },
    link: { color: colors.accent.ink, textDecorationLine: 'underline' },
    blockquote: { backgroundColor: colors.surface, borderLeftColor: colors.accent.ink, borderLeftWidth: 3, paddingHorizontal: spacing.md, paddingVertical: spacing.sm, marginVertical: spacing.sm },
    code_inline: { ...mono, color: colors.ink, backgroundColor: colors.sunken, borderRadius: radii.sm, paddingHorizontal: spacing.xs },
    code_block: { ...mono, color: colors.ink, backgroundColor: colors.code, borderColor: colors.hairline, borderWidth: 1, borderRadius: radii.sm, padding: spacing.md },
    fence: { ...mono, color: colors.ink, backgroundColor: colors.code, borderColor: colors.hairline, borderWidth: 1, borderRadius: radii.sm, padding: spacing.md },
    hr: { backgroundColor: colors.hairline, height: 1, marginVertical: spacing.md },
    table: { borderColor: colors.hairline, borderWidth: 1, borderRadius: radii.sm },
    th: { padding: spacing.sm, flex: 1, fontWeight: fontWeights.semibold }, td: { padding: spacing.sm, flex: 1 },
    tr: { borderBottomColor: colors.hairline, borderBottomWidth: 1, flexDirection: 'row' },
  });
}
