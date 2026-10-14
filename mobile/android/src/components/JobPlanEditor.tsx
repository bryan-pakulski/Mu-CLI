import React, { useMemo, useState } from 'react';
import { Pressable, StyleSheet, TextInput, View } from 'react-native';
import Markdown from 'react-native-markdown-display';
import { Text } from './Text';
import { useTheme } from '../theme/ThemeContext';
import type { ThemeColors } from '../theme/tokens';
import {
  type PlanRow, type PlanSection, planMarkdownStyles, planProse, replaceRows, rowsFrom,
} from './planMarkdown';
import { fontSizes, fontWeights, fonts, radii, spacing, touchTarget } from '../theme/tokens';

const PLAN_SECTIONS: PlanSection[] = ['Acceptance criteria', 'Checks'];

type Btn = { label: string; onPress: () => void; disabled?: boolean; tone?: 'accent' | 'danger' | 'quiet'; a11y?: string };

function PlanButton({ label, onPress, disabled, tone = 'quiet', a11y }: Btn) {
  const { colors } = useTheme();
  const fg = tone === 'danger' ? colors.error : tone === 'accent' ? colors.accentOn : colors.text;
  return <Pressable accessibilityRole="button" accessibilityLabel={a11y} accessibilityState={{ disabled: !!disabled }}
    disabled={disabled} onPress={onPress} android_ripple={{ color: colors.bgHover }}
    style={({ pressed }) => [styles.button, {
      borderColor: tone === 'accent' ? colors.accent : tone === 'danger' ? colors.danger : colors.border,
      backgroundColor: tone === 'accent'
        ? (pressed ? colors.accentHover : colors.accent)
        : pressed ? (tone === 'danger' ? colors.errorSubtle : colors.hover) : colors.raised,
      opacity: disabled ? 0.5 : 1,
    }]}>
    <Text variant="sm" style={{ color: fg, fontWeight: fontWeights.medium }}>{label}</Text>
  </Pressable>;
}

/** Read-only, wrapping cards for criteria and checks. */
export function PlanCriteriaCards({ markdown }: { markdown: string }) {
  const { colors } = useTheme();
  return <View style={styles.root}>{PLAN_SECTIONS.map(title => {
    const rows = rowsFrom(markdown, title);
    if (!rows.length) return null;
    return <View key={title} style={styles.root}>
      <SectionTitle title={title} count={rows.length} colors={colors} />
      {rows.map((row, i) => <View key={i} style={[styles.card, { backgroundColor: colors.surface, borderColor: colors.border }]}>
        <Text variant="xs" style={[styles.badge, { color: colors.textSoft, backgroundColor: colors.bgHover }]}>{row.key}</Text>
        <Text selectable variant="sm" style={[styles.wrap, title === 'Checks' && styles.mono]}>{row.text || '—'}</Text>
      </View>)}
    </View>;
  })}</View>;
}

function SectionTitle({ title, count, colors }: { title: string; count: number; colors: ThemeColors }) {
  return <View style={styles.sectionTitle}>
    <Text style={{ color: colors.text, fontWeight: fontWeights.semibold }}>{title}</Text>
    <Text variant="xs" dim style={styles.count}>{count}</Text>
  </View>;
}

/** Markdown first (preview / raw edit), then structured criteria + checks. All edit the same draft. */
export function JobPlanEditor({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const { colors } = useTheme();
  const [mode, setMode] = useState<'preview' | 'edit'>('preview');
  const md = useMemo(() => planMarkdownStyles(colors), [colors]);
  const input = { color: colors.text, borderColor: colors.border, backgroundColor: colors.bg };
  const prose = planProse(value);
  return <View style={styles.root}>
    <View style={styles.row}>
      <SectionTitle title="Plan" count={value.split('\n').length} colors={colors} />
      <View style={[styles.segment, { borderColor: colors.border }]}>
        {(['preview', 'edit'] as const).map(m => <Pressable key={m} accessibilityRole="button" accessibilityState={{ selected: mode === m }}
          onPress={() => setMode(m)} style={({ pressed }) => [styles.segmentItem, {
            backgroundColor: mode === m ? colors.raised : pressed ? colors.hover : 'transparent' }]}>
          <Text variant="sm" style={{ color: mode === m ? colors.text : colors.muted, fontWeight: mode === m ? fontWeights.semibold : fontWeights.medium }}>
            {m === 'preview' ? 'Preview' : 'Edit Markdown'}</Text>
        </Pressable>)}
      </View>
    </View>
    {mode === 'preview'
      ? <View style={[styles.card, { backgroundColor: colors.surface, borderColor: colors.border }]}>
          <Markdown style={md}>{prose || '_No plan text yet._'}</Markdown></View>
      : <TextInput accessibilityLabel="Plan Markdown" multiline value={value} onChangeText={onChange} autoCapitalize="none"
          style={[styles.editor, input]} />}
    {PLAN_SECTIONS.map(title => <RowsEditor key={title} title={title} value={value} onChange={onChange} />)}
  </View>;
}

function RowsEditor({ title, value, onChange }: { title: PlanSection; value: string; onChange: (v: string) => void }) {
  const { colors } = useTheme();
  const rows = rowsFrom(value, title);
  const input = { color: colors.text, borderColor: colors.border, backgroundColor: colors.bg };
  const update = (next: PlanRow[]) => onChange(replaceRows(value, title, next));
  const change = (index: number, patch: Partial<PlanRow>) => update(rows.map((row, i) => i === index ? { ...row, ...patch } : row));
  const move = (index: number, offset: number) => { const next = [...rows]; [next[index], next[index + offset]] = [next[index + offset], next[index]]; update(next); };
  return <View style={styles.root}>
    <SectionTitle title={title} count={rows.length} colors={colors} />
    {rows.map((row, index) => <View key={index} style={[styles.card, { backgroundColor: colors.surface, borderColor: colors.border }]}>
      <TextInput accessibilityLabel={`${title} ${index + 1} identifier`} value={row.key} onChangeText={key => change(index, { key })}
        autoCapitalize="none" style={[styles.keyInput, { color: colors.text, borderColor: colors.border, backgroundColor: colors.bg }]} />
      <TextInput accessibilityLabel={`${title} ${index + 1} text`} value={row.text} onChangeText={text => change(index, { text: text.replace(/\n/g, ' ') })}
        autoCapitalize="none" multiline scrollEnabled={false} style={[styles.input, input, title === 'Checks' && styles.mono]} />
      <View style={styles.row}>
        <PlanButton label="↑" a11y={`Move ${title} ${index + 1} up`} onPress={() => move(index, -1)} disabled={index === 0} />
        <PlanButton label="↓" a11y={`Move ${title} ${index + 1} down`} onPress={() => move(index, 1)} disabled={index === rows.length - 1} />
        <PlanButton label="Remove" a11y={`Remove ${title} ${index + 1}`} tone="danger" onPress={() => update(rows.filter((_, i) => i !== index))} />
      </View>
    </View>)}
    <PlanButton label={`Add ${title === 'Checks' ? 'check' : 'criterion'}`} a11y={`Add ${title === 'Checks' ? 'check' : 'criterion'}`} tone="accent" onPress={() => {
      let number = 1; const prefix = title === 'Checks' ? 'check' : 'AC-';
      while (rows.some(row => row.key === prefix + number)) number++;
      update([...rows, { key: prefix + number, text: '' }]);
    }} />
  </View>;
}

const styles = StyleSheet.create({
  root: { gap: spacing.sm, alignSelf: 'stretch' },
  row: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.xs, alignItems: 'center', justifyContent: 'space-between' },
  card: { gap: spacing.sm, borderWidth: 1, borderRadius: radii.panel, padding: spacing.md, alignSelf: 'stretch' },
  wrap: { flexShrink: 1, flexWrap: 'wrap' },
  mono: { fontFamily: fonts.mono, fontSize: fontSizes.sm },
  badge: { alignSelf: 'flex-start', fontFamily: fonts.mono, fontWeight: fontWeights.medium, borderRadius: radii.pill, paddingHorizontal: spacing.sm, paddingVertical: 2, overflow: 'hidden' },
  keyInput: { alignSelf: 'flex-start', minWidth: 72, minHeight: touchTarget, borderWidth: 1, borderRadius: radii.sm, paddingHorizontal: spacing.md, paddingVertical: spacing.xs, fontFamily: fonts.mono, fontSize: fontSizes.xs },
  input: { borderWidth: 1, borderRadius: radii.sm, paddingHorizontal: spacing.md, paddingVertical: spacing.sm, minHeight: touchTarget, textAlignVertical: 'top', flexShrink: 1 },
  button: { borderWidth: 1, borderRadius: radii.sm, paddingHorizontal: spacing.base, paddingVertical: spacing.sm, minHeight: touchTarget, minWidth: touchTarget, alignItems: 'center', justifyContent: 'center', alignSelf: 'flex-start' },
  editor: { borderWidth: 1, borderRadius: radii.sm, padding: spacing.md, minHeight: 320, textAlignVertical: 'top', fontFamily: fonts.mono, fontSize: fontSizes.sm },
  sectionTitle: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  count: { fontVariant: ['tabular-nums'] },
  segment: { flexDirection: 'row', borderWidth: 1, borderRadius: radii.sm, overflow: 'hidden' },
  segmentItem: { minHeight: touchTarget, justifyContent: 'center', paddingHorizontal: spacing.md, paddingVertical: spacing.sm },
});
