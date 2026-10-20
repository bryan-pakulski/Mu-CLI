// Mu page anatomy for Android (documentation/design/mu_style_guide.md §4, §5, §7, §11;
// documentation/plans/mobile_parity.md §5). Each piece mirrors a web component:
//
//   PageHeader      .w-pagehead / WashHead.set — title, state sentence, text tabs, ⋯ menu
//   StateSentence   .w-state — what is true now, in words, zeros left out
//   HeaderTabs      .w-pagetabs — text tabs, the active one underlined in the destination accent
//   StatusSign      .w-sign — one mark per status (◆ needs you, ● running, ✕ failed, ✓ done, ○ at rest)
//   Row / Rows      .w-row — sign, title, sub, when, actions; rows not cards; hairlines between
//   Board           .w-board — Now: sections of rows, needs-you first
//   PrimaryPill     §4.4 — the one primary action, a floating ink pill
//   ActionBar       the server's `actions` as buttons (primary first, the rest under More)
//   ActionSheet     a bottom sheet of actions
//   useServerActions confirm / input / typed confirmation for a server action, then run it
//
// Screens compose these; they never hand-build headers, rows or action menus.

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AccessibilityInfo, Animated, ScrollView, StyleSheet, View, type StyleProp, type ViewStyle } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useTheme } from '../theme/ThemeContext';
import { radii, spacing, toneInk, touchTarget, type Tone } from '../theme/tokens';
import { STATUS, asStatus } from '../theme/status';
import { Pressable } from './Pressable';
import { Text } from './Text';
import { Icon, type IconName } from './Icon';
import { Button, IconButton } from './Button';
import { Dialog, Sheet } from './Sheet';
import { TextField } from './Form';

/* ------------------------------------------------------------ status sign */

/** One status mark; `active` breathes (still under reduced motion). Status is never colour alone. */
export function StatusSign({ status, size = 14, testID }: { status: string; size?: number; testID?: string }) {
  const { colors } = useTheme();
  const row = STATUS[asStatus(status)];
  const pulse = useRef(new Animated.Value(1)).current;
  const active = asStatus(status) === 'active';
  useEffect(() => {
    if (!active) return undefined;
    let loop: Animated.CompositeAnimation | null = null;
    let cancelled = false;
    void Promise.resolve(AccessibilityInfo.isReduceMotionEnabled?.()).catch(() => false).then(reduce => {
      if (cancelled || reduce) return;
      loop = Animated.loop(Animated.sequence([
        Animated.timing(pulse, { toValue: 0.45, duration: 1200, useNativeDriver: true }),
        Animated.timing(pulse, { toValue: 1, duration: 1200, useNativeDriver: true }),
      ]));
      loop.start();
    });
    return () => { cancelled = true; loop?.stop(); };
  }, [active, pulse]);
  const ink = toneInk(colors, row.tone);
  if (active) {
    const dot = Math.round(size * 0.5);
    return (
      <View testID={testID} accessibilityLabel={row.label} style={{ width: size, height: size, alignItems: 'center', justifyContent: 'center' }}>
        <Animated.View style={{ width: dot, height: dot, borderRadius: dot, backgroundColor: colors.running.pigment, opacity: pulse }} />
      </View>
    );
  }
  return (
    <Text testID={testID} accessibilityLabel={row.label} style={{ width: size, textAlign: 'center', color: ['done', 'pending', 'skipped'].includes(asStatus(status)) ? colors.ink3 : ink, fontSize: size - 2, lineHeight: size + 4 }}>
      {row.icon}
    </Text>
  );
}

/* ------------------------------------------------------------ header */

export type StatePart = { text: string; tone?: 'attention' | 'danger' | 'quiet' | 'ok' | ''; onPress?: () => void };

/** The state sentence (§4.2): parts joined by a middle dot; zero counts are the caller's to drop. */
export function StateSentence({ parts, testID }: { parts: Array<StatePart | null | false | undefined>; testID?: string }) {
  const { colors } = useTheme();
  const shown = parts.filter(Boolean) as StatePart[];
  if (!shown.length) return null;
  return (
    <Text testID={testID} variant="caption" style={{ color: colors.ink2 }}>
      {shown.map((part, i) => {
        const color = part.tone === 'attention' ? colors.attention.ink : part.tone === 'danger' ? colors.danger.ink
          : part.tone === 'ok' ? colors.ok.ink : part.tone === 'quiet' ? colors.ink3 : colors.ink2;
        return (
          <Text key={i} variant="caption" style={{ color }} onPress={part.onPress} accessibilityRole={part.onPress ? 'link' : undefined}>
            {i ? <Text variant="caption" style={{ color: colors.ink3 }}>{'  ·  '}</Text> : null}{part.text}
          </Text>
        );
      })}
    </Text>
  );
}

export type HeaderTab = { key: string; label: string; count?: number | string | null };

/** Text tabs (§4.3): the active one in ink, underlined in the destination accent. */
export function HeaderTabs({ tabs, value, onChange, testID }: { tabs: HeaderTab[]; value: string; onChange: (key: string) => void; testID?: string }) {
  const { colors, accent } = useTheme();
  return (
    <ScrollView horizontal showsHorizontalScrollIndicator={false} testID={testID} accessibilityRole="tablist" contentContainerStyle={{ gap: spacing.base, paddingRight: spacing.base }}>
      {tabs.map(tab => {
        const on = tab.key === value;
        return (
          <Pressable key={tab.key} accessibilityRole="tab" accessibilityState={{ selected: on }} onPress={() => onChange(tab.key)} testID={testID ? `${testID}-${tab.key}` : undefined}
            style={{ minHeight: touchTarget, justifyContent: 'center', borderBottomWidth: 2, borderBottomColor: on ? accent.ink : 'transparent' }}>
            <Text variant="label" style={{ color: on ? colors.ink : colors.ink3 }}>
              {tab.label}{tab.count != null && tab.count !== '' && tab.count !== 0 ? <Text variant="caption" style={{ color: colors.ink3 }}>{`  ${tab.count}`}</Text> : null}
            </Text>
          </Pressable>
        );
      })}
    </ScrollView>
  );
}

export type MenuItem = { key: string; label: string; icon?: IconName; tone?: Tone; disabled?: boolean; reason?: string; onPress: () => void };

export type PageHeaderProps = {
  title: string;
  state?: Array<StatePart | null | false | undefined>;
  tabs?: HeaderTab[];
  tab?: string;
  onTab?: (key: string) => void;
  /** The ⋯ menu (secondary actions). */
  menu?: MenuItem[];
  /** A trailing accessory (e.g. Ask). */
  right?: React.ReactNode;
  back?: { label: string; onPress: () => void };
  testID?: string;
};

/**
 * The page header (§4): title, the state sentence under it, then text tabs. No chips. A stack
 * screen that renders it sets `headerShown: false` on its route and passes `back`.
 */
export function PageHeader({ title, state, tabs, tab, onTab, menu, right, back, testID }: PageHeaderProps) {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const [menuOpen, setMenuOpen] = useState(false);
  // The page header is the screen's only header (its route hides the navigator's), so it clears
  // the status bar itself.
  return (
    <View testID={testID} style={[styles.header, { borderBottomColor: colors.hairline, paddingTop: insets.top + spacing.md }]}>
      {back ? (
        <Pressable accessibilityRole="link" onPress={back.onPress} style={styles.back} hitSlop={10}>
          <Icon name="chevron-left" size="sm" color={colors.ink3} />
          <Text variant="caption" style={{ color: colors.ink3 }}>{back.label}</Text>
        </Pressable>
      ) : null}
      <View style={styles.titleRow}>
        <Text variant="title2" accessibilityRole="header" numberOfLines={2} style={{ flex: 1, color: colors.plum }}>{title}</Text>
        {right}
        {menu?.length ? <IconButton icon="more-horizontal" label="More actions" onPress={() => setMenuOpen(true)} testID={testID ? `${testID}-menu` : undefined} /> : null}
      </View>
      {state ? <StateSentence parts={state} testID={testID ? `${testID}-state` : undefined} /> : null}
      {tabs?.length && onTab ? <View style={{ marginTop: spacing.xs }}><HeaderTabs tabs={tabs} value={tab ?? tabs[0].key} onChange={onTab} testID={testID ? `${testID}-tabs` : undefined} /></View> : null}
      {menu?.length ? <ActionSheet visible={menuOpen} onClose={() => setMenuOpen(false)} items={menu} /> : null}
    </View>
  );
}

/* ------------------------------------------------------------ rows */

export type RowProps = {
  status?: string;
  title: React.ReactNode;
  sub?: React.ReactNode;
  when?: string;
  onPress?: () => void;
  onLongPress?: () => void;
  /** Trailing actions (buttons); always visible on touch. */
  actions?: React.ReactNode;
  /** Pinned: it needs you (a faint attention wash). */
  pinned?: boolean;
  selected?: boolean;
  accessibilityLabel?: string;
  testID?: string;
  style?: StyleProp<ViewStyle>;
};

/** One row (§5.1): sign · title / sub · when; actions below on narrow screens. */
export function Row({ status, title, sub, when, onPress, onLongPress, actions, pinned, selected, accessibilityLabel, testID, style }: RowProps) {
  const { colors } = useTheme();
  const bg = selected ? colors.sunken : pinned ? colors.attention.wash : 'transparent';
  const body = (
    <View style={styles.rowMain}>
      {status ? <View style={styles.rowSign}><StatusSign status={status} /></View> : null}
      <View style={{ flex: 1, minWidth: 0 }}>
        <View style={styles.rowTitleLine}>
          {typeof title === 'string' ? <Text variant="body" numberOfLines={2} style={{ flex: 1, color: colors.ink }}>{title}</Text> : <View style={{ flex: 1 }}>{title}</View>}
          {when ? <Text variant="caption" numeric style={{ color: colors.ink3 }}>{when}</Text> : null}
        </View>
        {sub ? (typeof sub === 'string' ? <Text variant="caption" numberOfLines={2} style={{ color: colors.ink3 }}>{sub}</Text> : sub) : null}
      </View>
    </View>
  );
  return (
    <View testID={testID} style={[styles.row, { backgroundColor: bg }, style]}>
      {onPress || onLongPress ? (
        <Pressable accessibilityRole="button" accessibilityLabel={accessibilityLabel} onPress={onPress} onLongPress={onLongPress} style={{ minHeight: 48, justifyContent: 'center' }}>{body}</Pressable>
      ) : <View accessible accessibilityLabel={accessibilityLabel} style={{ minHeight: 48, justifyContent: 'center' }}>{body}</View>}
      {actions ? <View style={[styles.rowActions, status ? { paddingLeft: 28 } : null]}>{actions}</View> : null}
    </View>
  );
}

/** Rows with hairlines between them; `empty` when there are none. */
export function Rows({ children, empty, label, testID }: { children?: React.ReactNode; empty?: string; label?: string; testID?: string }) {
  const { colors } = useTheme();
  const items = React.Children.toArray(children).filter(Boolean);
  if (!items.length) return empty ? <Text testID={testID} variant="caption" style={{ color: colors.ink3, paddingVertical: spacing.sm }}>{empty}</Text> : null;
  return (
    <View testID={testID} accessibilityLabel={label}>
      {items.map((child, i) => (
        <View key={i} style={i ? { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.hairline } : null}>{child}</View>
      ))}
    </View>
  );
}

/** A Now board section: a quiet title (and count), then its rows. */
export function BoardSection({ title, count, children, empty, right, testID }: { title: string; count?: number; children?: React.ReactNode; empty?: string; right?: React.ReactNode; testID?: string }) {
  const { colors } = useTheme();
  const items = React.Children.toArray(children).filter(Boolean);
  if (!items.length && !empty) return null;
  return (
    <View testID={testID} style={{ marginBottom: spacing.lg }}>
      <View style={styles.sectionHead}>
        <Text variant="label" accessibilityRole="header" style={{ flex: 1, color: colors.ink2 }}>{title}{count ? <Text variant="caption" style={{ color: colors.ink3 }}>{`  ${count}`}</Text> : null}</Text>
        {right}
      </View>
      <Rows empty={empty}>{items}</Rows>
    </View>
  );
}

/** The Now board (§5.2): sections of rows on the page, needs-you first. */
export function Board({ children, testID }: { children?: React.ReactNode; testID?: string }) {
  return <View testID={testID}>{children}</View>;
}

/* ------------------------------------------------------------ primary pill */

/** The one primary action (§4.4): a floating ink pill above the tab bar. */
export function PrimaryPill({ label, icon = 'plus', onPress, testID, bottom = 0 }: { label: string; icon?: IconName; onPress: () => void; testID?: string; bottom?: number }) {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  return (
    <View pointerEvents="box-none" style={[StyleSheet.absoluteFill, { justifyContent: 'flex-end', alignItems: 'flex-end', padding: spacing.base, paddingBottom: spacing.base + bottom + Math.max(0, insets.bottom - 34) }]}>
      <Pressable testID={testID} accessibilityRole="button" accessibilityLabel={label} onPress={onPress} haptic="light"
        style={({ pressed }) => [styles.pill, { backgroundColor: colors.ink, opacity: pressed ? 0.85 : 1, shadowColor: colors.shadow }]}>
        <Icon name={icon} size="sm" color={colors.onInk} />
        <Text variant="label" style={{ color: colors.onInk }}>{label}</Text>
      </Pressable>
    </View>
  );
}

/* ------------------------------------------------------------ server actions */

/**
 * An action the server offers (job `actions`, run/node/tool `actions`): the client never decides
 * what is allowed. `confirm` asks first; `input` asks for a value (and `expect` makes it a typed
 * confirmation); `danger` styles it destructive; disabled actions explain why.
 */
export type ServerAction = {
  id: string;
  label: string;
  enabled?: boolean;
  primary?: boolean;
  danger?: boolean;
  tone?: string;
  reason?: string;
  confirm?: string;
  hidden?: boolean;
  args?: Record<string, unknown>;
  input?: { arg: string; label?: string; expect?: string; optional?: boolean; multiline?: boolean; /** Prefill. */ value?: string; /** Helper text under the field. */ hint?: string };
  dialog?: string;
};

type Pending = { action: ServerAction; value: string; resolve: (args: Record<string, unknown> | null) => void };

/**
 * Confirm / input / typed confirmation for server actions. `ask(action)` resolves to the args to
 * send (or null when cancelled); render `element` once in the screen.
 */
export function useServerActions() {
  const [pending, setPending] = useState<Pending | null>(null);
  const ask = useCallback((action: ServerAction): Promise<Record<string, unknown> | null> => {
    if (!action.confirm && !action.input) return Promise.resolve({ ...(action.args || {}) });
    return new Promise(resolve => setPending({ action, value: action.input?.value ?? '', resolve }));
  }, []);
  const close = (result: Record<string, unknown> | null) => {
    pending?.resolve(result);
    setPending(null);
  };
  const action = pending?.action;
  const input = action?.input;
  const typedOk = !input?.expect || pending?.value.trim() === input.expect;
  const valueOk = !input || input.optional || (pending?.value.trim() ?? '') !== '';
  const element = (
    <Dialog visible={!!pending} onClose={() => close(null)} title={action?.label || ''} testID="server-action-dialog"
      footer={<>
        <Button variant="secondary" label="Cancel" onPress={() => close(null)} />
        <Button variant={action?.danger ? 'danger' : 'primary'} label={action?.label || 'OK'} disabled={!typedOk || !valueOk}
          onPress={() => close({ ...(action?.args || {}), ...(input && (pending?.value || !input.optional) ? { [input.arg]: pending?.value.trim() } : {}) })} testID="server-action-ok" />
      </>}>
      {action?.confirm ? <Text variant="body" style={{ marginBottom: spacing.sm }}>{action.confirm}</Text> : null}
      {input ? (
        <View style={{ gap: spacing.xs }}>
          {input.label ? <Text variant="label">{input.label}</Text> : null}
          {input.expect ? <Text variant="caption">Type {input.expect} to confirm.</Text> : null}
          {input.hint ? <Text variant="caption" tone="ink3">{input.hint}</Text> : null}
          <TextField value={pending?.value || ''} onChangeText={v => setPending(p => (p ? { ...p, value: v } : p))} autoCapitalize="none" autoCorrect={false}
            multiline={input.multiline} testID="server-action-input" accessibilityLabel={input.label || action?.label} />
        </View>
      ) : null}
    </Dialog>
  );
  return { ask, element };
}

/**
 * The server's actions as a bar: enabled primary actions as buttons (the first is the page's one
 * primary), everything else under More (disabled ones with their reason).
 */
export function ActionBar({ actions, onAction, only, exclude = [], testID }: {
  actions: ServerAction[] | null | undefined;
  onAction: (action: ServerAction) => void;
  only?: string[];
  exclude?: string[];
  testID?: string;
}) {
  const [more, setMore] = useState(false);
  const list = useMemo(() => {
    let out = (actions || []).filter(a => !a.hidden && !exclude.includes(a.id));
    if (only) out = only.map(id => out.find(a => a.id === id)).filter(Boolean) as ServerAction[];
    return out;
  }, [actions, only, exclude]);
  const primaries = list.filter(a => a.enabled !== false && a.primary);
  const rest = list.filter(a => !primaries.includes(a));
  if (!list.length) return null;
  return (
    <View testID={testID} style={styles.actionBar}>
      {primaries.map((a, i) => (
        <Button key={a.id} label={a.label} variant={a.danger ? 'danger' : i === 0 ? 'primary' : 'secondary'} onPress={() => onAction(a)} testID={testID ? `${testID}-${a.id}` : undefined} />
      ))}
      {rest.length ? <Button label={primaries.length ? 'More' : 'Actions'} variant="secondary" icon="more-horizontal" onPress={() => setMore(true)} testID={testID ? `${testID}-more` : undefined} /> : null}
      <ActionSheet visible={more} onClose={() => setMore(false)} items={rest.map(a => ({
        key: a.id, label: a.label, tone: a.danger ? 'danger' : undefined, disabled: a.enabled === false, reason: a.reason, onPress: () => onAction(a),
      }))} />
    </View>
  );
}

/** A bottom sheet of actions; disabled ones show why. */
export function ActionSheet({ visible, onClose, title, items }: { visible: boolean; onClose: () => void; title?: string; items: MenuItem[] }) {
  const { colors } = useTheme();
  return (
    <Sheet visible={visible} onClose={onClose} title={title} testID="action-sheet">
      {items.map(item => (
        <Pressable key={item.key} accessibilityRole="menuitem" accessibilityState={{ disabled: item.disabled }}
          onPress={() => { if (item.disabled) return; onClose(); item.onPress(); }} testID={`action-sheet-${item.key}`}
          style={({ pressed }) => [styles.menuItem, { opacity: item.disabled ? 0.55 : pressed ? 0.7 : 1 }]}>
          {item.icon ? <Icon name={item.icon} size="sm" color={item.tone ? toneInk(colors, item.tone) : colors.ink2} /> : null}
          <View style={{ flex: 1 }}>
            <Text variant="body" style={{ color: item.tone ? toneInk(colors, item.tone) : colors.ink }}>{item.label}</Text>
            {item.disabled && item.reason ? <Text variant="caption" style={{ color: colors.ink3 }}>{item.reason}</Text> : null}
          </View>
        </Pressable>
      ))}
    </Sheet>
  );
}

const styles = StyleSheet.create({
  header: { paddingHorizontal: spacing.base, paddingTop: spacing.md, paddingBottom: spacing.sm, gap: 2, borderBottomWidth: StyleSheet.hairlineWidth },
  back: { flexDirection: 'row', alignItems: 'center', gap: 2, alignSelf: 'flex-start', minHeight: 28 },
  titleRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, minHeight: touchTarget },
  row: { paddingVertical: spacing.sm, paddingHorizontal: spacing.sm, borderRadius: radii.md },
  rowMain: { flexDirection: 'row', alignItems: 'flex-start', gap: spacing.sm },
  rowSign: { width: 20, alignItems: 'center', paddingTop: 2 },
  rowTitleLine: { flexDirection: 'row', alignItems: 'baseline', gap: spacing.sm },
  rowActions: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, marginTop: spacing.xs },
  sectionHead: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: spacing.sm, marginBottom: spacing.xs },
  pill: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, height: 48, paddingHorizontal: spacing.lg, borderRadius: radii.pill, shadowOpacity: 0.18, shadowRadius: 12, shadowOffset: { width: 0, height: 6 }, elevation: 6 },
  actionBar: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  menuItem: { flexDirection: 'row', alignItems: 'center', gap: spacing.md, minHeight: touchTarget, paddingVertical: spacing.sm },
});
