// A turn's tool calls (web wash/toolcard.js renderToolCards; model in ./toolList.ts).
//
// A finished turn folds into one summary line, "Read 3 files · ran 2 commands" (failed count in
// danger ink), that expands to the rows; a running turn stays open so you can watch it work.
// Rows: single calls are ToolCards; 3+ consecutive finished same-kind calls are one group row
// ("Read 6 files · a.py, b.py, …"); thinking rows and notes keep their place.

import React, { useState } from 'react';
import { View } from 'react-native';
import { useTheme } from '../theme/ThemeContext';
import { radii, spacing } from '../theme/tokens';
import { useGlass } from './Glass';
import { Icon, type IconName } from './Icon';
import { InkDrop } from './Progress';
import { Pressable } from './Pressable';
import { Text } from './Text';
import { ToolCard, type ToolCallView } from './ToolCard';
import {
  TOOL_KINDS, callItems, formatDuration, groupLabel, middleTruncate, turnSummary,
  type ThinkingItem, type ToolCardItem, type ToolGroupItem, type ToolListItem,
} from './toolList';

/** A model card as the ToolCard view. */
export function cardView(card: ToolCardItem): ToolCallView {
  const duration = card.duration != null ? card.duration : card.doneAt && card.startedAt ? card.doneAt - card.startedAt : undefined;
  return {
    id: card.id, name: card.name, args: card.args, result: card.resultText,
    status: card.state === 'running' ? 'running' : card.state === 'error' || card.state === 'blocked' ? 'failed' : 'done',
    startedAt: card.state === 'running' ? card.startedAt || undefined : undefined,
    durationMs: duration,
  };
}

function GroupRow({ group }: { group: ToolGroupItem }) {
  const { colors } = useTheme();
  const glass = useGlass();
  const [open, setOpen] = useState(false);
  const failed = group.cards.some(c => c.state === 'error');
  const targets = group.cards.map(c => (c.target ? String(c.target).split('/').pop() : c.name));
  const total = group.cards.reduce((sum, c) => sum + (c.doneAt && c.startedAt ? c.doneAt - c.startedAt : c.duration || 0), 0);
  const label = groupLabel(group);
  return (
    <View testID={`tool-group-${group.id}`} style={{ gap: spacing.xs }}>
      <Pressable accessibilityLabel={`${label}${failed ? ', one failed' : ''}`} accessibilityState={{ expanded: open }} scale={false} onPress={() => setOpen(o => !o)}
        style={({ pressed }) => [glass.style('subtle'), { minHeight: 44, flexDirection: 'row', alignItems: 'center', gap: spacing.sm, paddingHorizontal: spacing.md, borderRadius: radii.md }, pressed ? { backgroundColor: colors.hairline } : null]}>
        <Text variant="label" importantForAccessibility="no" style={{ width: 14, textAlign: 'center', color: failed ? colors.danger.ink : colors.ink3 }}>{failed ? '✗' : '✓'}</Text>
        <Icon name={TOOL_KINDS[group.kind].icon as IconName} size="sm" color={colors.ink3} />
        <Text variant="label">{label}</Text>
        <Text variant="monoSm" tone="ink3" numberOfLines={1} style={{ flex: 1 }}>{middleTruncate(targets.join(', '), 120)}</Text>
        {total ? <Text variant="caption" tone="ink3" numeric>{formatDuration(total)}</Text> : null}
        <Text variant="caption" tone="ink3" importantForAccessibility="no">{open ? '▾' : '›'}</Text>
      </Pressable>
      {open ? group.cards.map(c => <ToolCard key={c.id} call={cardView(c)} />) : null}
    </View>
  );
}

function ThinkingRow({ item }: { item: ThinkingItem }) {
  const { colors } = useTheme();
  const [open, setOpen] = useState<boolean | null>(null);
  const shown = open ?? item.live;
  const words = item.text.trim() ? item.text.trim().split(/\s+/).length : 0;
  return (
    <View>
      <Pressable accessibilityLabel={item.live ? 'Thinking' : `Thought, ${words} words`} accessibilityState={{ expanded: shown }} scale={false}
        onPress={() => setOpen(!shown)} style={{ minHeight: 36, flexDirection: 'row', alignItems: 'center', gap: spacing.sm }}>
        {item.live ? <InkDrop tone="neutral" size={8} /> : <Text variant="caption" tone="ink3" importantForAccessibility="no">{shown ? '▾' : '›'}</Text>}
        <Text variant="label" tone="ink2">{item.live ? 'Thinking' : 'Thought'}</Text>
        {words && !item.live ? <Text variant="caption" tone="ink3">{words} words</Text> : null}
      </Pressable>
      {shown ? <Text variant="caption" style={{ color: colors.ink2, paddingLeft: spacing.lg }}>{item.text}</Text> : null}
    </View>
  );
}

function Items({ items }: { items: ToolListItem[] }) {
  const { colors } = useTheme();
  return (
    <View style={{ gap: spacing.xs }}>
      {items.map(item => {
        if (item.type === 'group') return <GroupRow key={item.id} group={item} />;
        if (item.type === 'thinking') return <ThinkingRow key={item.id} item={item} />;
        if (item.type === 'note') {
          return (
            <View key={item.id} style={{ flexDirection: 'row', alignItems: 'center', gap: spacing.sm }}>
              <Icon name="info" size="sm" color={colors.ink3} />
              <Text variant="caption" tone="ink2" style={{ flex: 1 }}>{item.text}</Text>
            </View>
          );
        }
        return <ToolCard key={item.id} call={cardView(item)} testID={`tool-card-${item.id}`} />;
      })}
    </View>
  );
}

export function ToolList({ items, running, testID = 'tool-list' }: { items: ToolListItem[]; running?: boolean; testID?: string }) {
  const { colors } = useTheme();
  const [open, setOpen] = useState(false);
  if (!items.length) {
    return running ? (
      <View testID={testID} style={{ flexDirection: 'row', alignItems: 'center', gap: spacing.sm, minHeight: 32 }}>
        <InkDrop tone="running" size={8} />
        <Text variant="caption" tone="ink2">Working…</Text>
      </View>
    ) : null;
  }
  const calls = callItems(items);
  if (!running && calls.length) {
    const sum = turnSummary(items);
    return (
      <View testID={testID} style={{ gap: spacing.xs }}>
        <Pressable testID={`${testID}-summary`} accessibilityRole="button" accessibilityLabel={`${sum.text}${sum.failed ? `, ${sum.failed} failed` : ''}`}
          accessibilityState={{ expanded: open }} scale={false} onPress={() => setOpen(o => !o)}
          style={{ minHeight: 36, flexDirection: 'row', alignItems: 'center', gap: spacing.sm, alignSelf: 'flex-start' }}>
          <Text variant="caption" tone="ink3" importantForAccessibility="no">{open ? '▾' : '▸'}</Text>
          {sum.failed ? <Text variant="label" importantForAccessibility="no" style={{ color: colors.danger.ink }}>✗</Text> : null}
          <Text variant="label" tone="ink2">{sum.text}</Text>
          {sum.failed ? <Text variant="caption" style={{ color: colors.danger.ink }}>{sum.failed} failed</Text> : null}
        </Pressable>
        {open ? <Items items={items} /> : null}
      </View>
    );
  }
  return <View testID={testID}><Items items={items} /></View>;
}
