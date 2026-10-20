// The plan editor (job page → Plan): Markdown first (preview or raw edit), then the acceptance
// criteria and checks as editable rows. Every part edits the same draft Markdown.

import React, { useState } from 'react';
import { StyleSheet, View } from 'react-native';
import { type PlanRow, type PlanSection, planProse, replaceRows, rowsFrom } from './planMarkdown';
import { spacing } from '../theme/tokens';
import { Button, Markdown, Panel, Segmented, Text, TextField } from '../ui';

const PLAN_SECTIONS: PlanSection[] = ['Acceptance criteria', 'Checks'];

/** Read-only criteria and checks. */
export function PlanCriteriaCards({ markdown }: { markdown: string }) {
  return <View style={styles.col}>{PLAN_SECTIONS.map(title => {
    const rows = rowsFrom(markdown, title);
    if (!rows.length) return null;
    return <Panel key={title} title={title} badge={rows.length}>
      {rows.map((row, i) => (
        <View key={i} style={styles.row}>
          <Text variant="monoSm" tone="ink3">{row.key}</Text>
          <Text selectable variant={title === 'Checks' ? 'monoSm' : 'body'} style={styles.grow}>{row.text || '—'}</Text>
        </View>
      ))}
    </Panel>;
  })}</View>;
}

export function JobPlanEditor({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const [mode, setMode] = useState<'preview' | 'edit'>('preview');
  const prose = planProse(value);
  return <View style={styles.col}>
    <Segmented<'preview' | 'edit'> accessibilityLabel="Plan view" value={mode} onChange={setMode}
      options={[{ value: 'preview', label: 'Preview' }, { value: 'edit', label: 'Edit Markdown' }]} />
    {mode === 'preview'
      ? <Panel><Markdown variant="document">{prose || '_No plan text yet._'}</Markdown></Panel>
      : <TextField accessibilityLabel="Plan Markdown" multiline mono value={value} onChangeText={onChange} autoCapitalize="none" autoCorrect={false} style={styles.editor} />}
    {PLAN_SECTIONS.map(title => <RowsEditor key={title} title={title} value={value} onChange={onChange} />)}
  </View>;
}

function RowsEditor({ title, value, onChange }: { title: PlanSection; value: string; onChange: (v: string) => void }) {
  const rows = rowsFrom(value, title);
  const update = (next: PlanRow[]) => onChange(replaceRows(value, title, next));
  const change = (index: number, patch: Partial<PlanRow>) => update(rows.map((row, i) => i === index ? { ...row, ...patch } : row));
  const move = (index: number, offset: number) => { const next = [...rows]; [next[index], next[index + offset]] = [next[index + offset], next[index]]; update(next); };
  const noun = title === 'Checks' ? 'check' : 'criterion';
  return <Panel title={title} badge={rows.length}>
    {rows.map((row, index) => <View key={index} style={styles.item}>
      <TextField accessibilityLabel={`${title} ${index + 1} identifier`} value={row.key} onChangeText={key => change(index, { key })} autoCapitalize="none" autoCorrect={false} mono style={styles.key} />
      <TextField accessibilityLabel={`${title} ${index + 1} text`} value={row.text} onChangeText={text => change(index, { text: text.replace(/\n/g, ' ') })}
        autoCapitalize="none" multiline scrollEnabled={false} mono={title === 'Checks'} />
      <View style={styles.row}>
        <Button small variant="secondary" label="↑" accessibilityLabel={`Move ${title} ${index + 1} up`} onPress={() => move(index, -1)} disabled={index === 0} />
        <Button small variant="secondary" label="↓" accessibilityLabel={`Move ${title} ${index + 1} down`} onPress={() => move(index, 1)} disabled={index === rows.length - 1} />
        <Button small variant="ghost" tone="danger" label="Remove" accessibilityLabel={`Remove ${title} ${index + 1}`} onPress={() => update(rows.filter((_, i) => i !== index))} />
      </View>
    </View>)}
    <Button small variant="secondary" icon="plus" label={`Add ${noun}`} accessibilityLabel={`Add ${noun}`} onPress={() => {
      let number = 1; const prefix = title === 'Checks' ? 'check' : 'AC-';
      while (rows.some(row => row.key === prefix + number)) number++;
      update([...rows, { key: prefix + number, text: '' }]);
    }} />
  </Panel>;
}

const styles = StyleSheet.create({
  col: { gap: spacing.sm, alignSelf: 'stretch' },
  row: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, alignItems: 'center' },
  grow: { flex: 1 },
  item: { gap: spacing.xs, paddingBottom: spacing.sm },
  key: { alignSelf: 'flex-start', minWidth: 96 },
  editor: { minHeight: 320 },
});
