// The one diff component (G§9.11), unified on mobile: additions on the ok
// wash with a + gutter in ok ink, deletions on the danger wash with a −
// gutter in danger ink, context on canvas, hunk headers sunken in ink-3.
// Line numbers in mono-sm ink-3. The file header shows the path and +N −M.

import React, { useState } from 'react';
import { ScrollView, View } from 'react-native';
import { useTheme } from '../theme/ThemeContext';
import { fonts, radii, spacing, typeStyle } from '../theme/tokens';
import { parsePatch, type DiffFile, type DiffLine } from '../components/jobViews';
import { Pressable } from './Pressable';
import { Text } from './Text';

/** Does this text look like a unified diff? */
export function looksLikeDiff(text: string | null | undefined): boolean {
  const s = String(text || '');
  return /^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@/m.test(s) || /^diff --git /m.test(s);
}

export function DiffLines({ lines, maxLines, testID }: { lines: DiffLine[]; maxLines?: number; testID?: string }) {
  const { colors } = useTheme();
  const mono = { ...typeStyle('mono-sm'), fontFamily: fonts.mono };
  const shown = maxLines ? lines.slice(0, maxLines) : lines;
  return (
    <View testID={testID}>
      {shown.map((l, i) => {
        if (l.kind === 'hunk') {
          return <Text key={i} variant="monoSm" numberOfLines={1} style={[mono, { color: colors.ink3, backgroundColor: colors.sunken, paddingHorizontal: spacing.sm, paddingVertical: 2 }]}>{l.text}</Text>;
        }
        if (l.kind === 'note') return <Text key={i} variant="monoSm" style={[mono, { color: colors.ink3, paddingHorizontal: spacing.sm }]}>{l.text}</Text>;
        const add = l.kind === 'add';
        const del = l.kind === 'del';
        const bg = add ? colors.ok.wash : del ? colors.danger.wash : 'transparent';
        const sign = add ? '+' : del ? '−' : ' ';
        const signColor = add ? colors.ok.ink : del ? colors.danger.ink : colors.ink3;
        return (
          <View key={i} style={{ flexDirection: 'row', backgroundColor: bg }}>
            <Text variant="monoSm" numeric style={[mono, { color: colors.ink3, width: 36, textAlign: 'right', paddingRight: spacing.xs }]}>{del ? l.oldNo : l.newNo}</Text>
            <Text variant="monoSm" accessibilityLabel={add ? 'added' : del ? 'removed' : undefined} style={[mono, { color: signColor, width: 14, textAlign: 'center' }]}>{sign}</Text>
            <Text variant="monoSm" selectable style={[mono, { color: colors.ink, flex: 1, paddingRight: spacing.sm }]}>{l.text}</Text>
          </View>
        );
      })}
    </View>
  );
}

export function DiffFileHeader({ file, open, onToggle }: { file: Pick<DiffFile, 'path' | 'added' | 'removed'>; open?: boolean; onToggle?: () => void }) {
  const { colors } = useTheme();
  const body = (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: spacing.sm, paddingHorizontal: spacing.sm, minHeight: 36 }}>
      {onToggle ? <Text variant="caption" tone="ink3">{open ? '▾' : '▸'}</Text> : null}
      <Text variant="mono" numberOfLines={1} ellipsizeMode="middle" style={{ flex: 1 }}>{file.path}</Text>
      <Text variant="monoSm" style={{ color: colors.ok.ink }}>+{file.added}</Text>
      <Text variant="monoSm" style={{ color: colors.danger.ink }}>−{file.removed}</Text>
    </View>
  );
  return onToggle ? (
    <Pressable accessibilityLabel={`${file.path}, ${file.added} added, ${file.removed} removed`} accessibilityState={{ expanded: !!open }} scale={false} onPress={onToggle}>{body}</Pressable>
  ) : body;
}

/** Unified diff of a patch string (one or more files). */
export function DiffView({ patch, maxLines = 400, testID }: { patch: string; maxLines?: number; testID?: string }) {
  const { colors } = useTheme();
  const files = parsePatch(patch);
  const [closed, setClosed] = useState<Record<string, boolean>>({});
  return (
    <View testID={testID} style={{ borderRadius: radii.sm, borderWidth: 1, borderColor: colors.hairline, overflow: 'hidden', backgroundColor: colors.canvas }}>
      {files.map(f => (
        <View key={f.path}>
          {files.length > 1 || f.path !== 'diff' ? (
            <DiffFileHeader file={f} open={!closed[f.path]} onToggle={() => setClosed(c => ({ ...c, [f.path]: !c[f.path] }))} />
          ) : null}
          {closed[f.path] ? null : (
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ minWidth: '100%' }}>
              <View style={{ minWidth: '100%' }}>
                <DiffLines lines={f.lines} maxLines={maxLines} />
              </View>
            </ScrollView>
          )}
        </View>
      ))}
    </View>
  );
}
