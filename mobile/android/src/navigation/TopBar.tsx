// The tab shell's one top bar, fixed and compact: the focused page's title (and accessory) on the
// left, the destinations as small icons (with what-needs-you badges), then one Options menu that
// collects the page's extra actions and the app's own (settings, connection, provider, pairing).
// A tab's PageHeader hands its title, accessory and ⋯ items over through TopBarContext, so the
// page keeps only its text tabs / state sentence and the content gets the rest of the screen.
// Options opens as a dropdown hanging from the bar's right end.

import React, { useMemo, useState } from 'react';
import { Modal, ScrollView, StyleSheet, useWindowDimensions, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import type { BottomTabBarProps } from '@react-navigation/bottom-tabs';
import { useTheme } from '../theme/ThemeContext';
import { elevation, radii, spacing, toneInk, touchTarget } from '../theme/tokens';
import { useConnectionStore } from '../store/connection';
import { badgeText } from '../store/orchBadge';
import { Icon, Text } from '../ui';
import { GlassChrome } from '../ui/Glass';
import type { MenuItem } from '../ui/Mu';
import { useBarPage } from '../store/barPage';
import { Pressable } from '../ui/Pressable';
import { navigateTo } from './nav';
import type { TabSpec } from './types';

export type TabBadges = Partial<Record<string, { count: number; summary: string }>>;

/** The app-level options every page shares, after the page's own. */
export function appOptions(pairingRequired: boolean): MenuItem[] {
  return [
    { key: 'app-settings', label: 'App settings', icon: 'settings', onPress: () => navigateTo('Settings') },
    { key: 'providers', label: 'Provider and model', icon: 'cpu', onPress: () => navigateTo('Providers') },
    { key: 'connection', label: 'Connection', icon: 'wifi', onPress: () => navigateTo('Connection') },
    { key: 'pair', label: pairingRequired ? 'Pair this phone' : 'Pairing', icon: 'link', tone: pairingRequired ? 'attention' : undefined, onPress: () => navigateTo('Pair') },
  ];
}

export function TopBar({ state, navigation, tabs, badges }: Pick<BottomTabBarProps, 'state' | 'navigation'> & { tabs: TabSpec[]; badges: TabBadges }) {
  const page = useBarPage(s => s.page);
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const pairingRequired = useConnectionStore(s => s.pairingRequired);
  const [open, setOpen] = useState(false);
  const [height, setHeight] = useState(0);
  const app = useMemo(() => appOptions(pairingRequired), [pairingRequired]);
  const focusedSpec = tabs.find(t => t.name === state.routes[state.index]?.name);
  const title = page?.title || focusedSpec?.label || '';

  return (
    <View style={[styles.bar, { paddingTop: insets.top, borderBottomColor: colors.hairline }]} testID="top-bar"
      onLayout={e => setHeight(e.nativeEvent.layout.height)}>
      <GlassChrome level={1} edges={false} style={StyleSheet.absoluteFill} />
      <View style={styles.row}>
        <Text variant="label" accessibilityRole="header" numberOfLines={1} testID="top-bar-title"
          style={[styles.title, { color: colors.plum }]}>{title}</Text>
        {page?.right ? <View style={styles.accessory}>{page.right}</View> : null}
        <View style={styles.tabs} accessibilityRole="tablist">
          {state.routes.map((route, index) => {
            const spec = tabs.find(t => t.name === route.name);
            if (!spec) return null;
            const focused = state.index === index;
            const accent = colors.destination[spec.destination];
            const badge = badges[route.name];
            const count = badge ? badgeText(badge.count) : undefined;
            return (
              <Pressable key={route.key} accessibilityRole="tab" accessibilityState={{ selected: focused }}
                accessibilityLabel={badge?.count ? `${spec.label}, ${badge.summary || `${badge.count} need you`}` : spec.label}
                testID={`tab-${spec.name.toLowerCase()}`} scale={false} style={styles.tab} hitSlop={{ top: 6, bottom: 6 }}
                onPress={() => {
                  const event = navigation.emit({ type: 'tabPress', target: route.key, canPreventDefault: true });
                  if (!focused && !event.defaultPrevented) navigation.navigate(route.name as never);
                }}>
                <View style={[styles.pill, { backgroundColor: focused ? accent.wash : 'transparent' }]}>
                  <Icon name={spec.icon} size="sm" color={focused ? accent.ink : colors.ink3} />
                </View>
                {count ? (
                  <View style={[styles.badge, { backgroundColor: colors.attention.wash }]} pointerEvents="none">
                    <Text variant="monoSm" numeric style={{ color: colors.attention.ink, fontSize: 10, lineHeight: 14 }}>{count}</Text>
                  </View>
                ) : null}
              </Pressable>
            );
          })}
          <Pressable accessibilityRole="button" accessibilityLabel="Options" testID="top-bar-options" scale={false} style={styles.tab}
            hitSlop={{ top: 6, bottom: 6, right: 6 }} onPress={() => setOpen(true)}>
            <View style={styles.pill}>
              <Icon name="more-horizontal" size="sm" color={pairingRequired ? colors.attention.ink : colors.ink2} />
            </View>
          </Pressable>
        </View>
      </View>
      <OptionsDropdown visible={open} onClose={() => setOpen(false)} top={height} page={page} app={app} />
    </View>
  );
}

/** The Options menu: a raised panel hanging from the bar's right end, the page's options first. */
export function OptionsDropdown({ visible, onClose, top, page, app }: { visible: boolean; onClose: () => void; top: number; page: { title: string; items: MenuItem[] } | null; app: MenuItem[] }) {
  const { colors } = useTheme();
  const { height } = useWindowDimensions();
  const sections = [
    page?.items.length ? { key: 'page', title: page.title, items: page.items } : null,
    { key: 'app', title: 'App', items: app },
  ].filter(Boolean) as Array<{ key: string; title: string; items: MenuItem[] }>;
  return (
    <Modal visible={visible} transparent animationType="fade" statusBarTranslucent onRequestClose={onClose}>
      <Pressable accessibilityLabel="Close options" scale={false} style={StyleSheet.absoluteFill} onPress={onClose} testID="options-backdrop" />
      <View style={[styles.menu, elevation(2, colors), { top: top + spacing.xs, maxHeight: height - top - spacing.xl }]} testID="options-menu">
        <ScrollView bounces={false} contentContainerStyle={{ paddingVertical: spacing.xs }}>
          {sections.map((section, i) => (
            <View key={section.key}>
              {i ? <View style={[styles.rule, { backgroundColor: colors.hairline }]} /> : null}
              <Text variant="eyebrow" tone="ink3" numberOfLines={1} style={styles.heading}>{section.title}</Text>
              {section.items.map(item => {
                const ink = item.tone ? toneInk(colors, item.tone) : colors.ink;
                return (
                  <Pressable key={item.key} accessibilityRole="menuitem" accessibilityState={{ disabled: item.disabled }} scale={false}
                    testID={`options-${item.key}`} onPress={() => { if (item.disabled) return; onClose(); item.onPress(); }}
                    style={({ pressed }: { pressed: boolean }) => [styles.item, { backgroundColor: pressed ? colors.sunken : 'transparent', opacity: item.disabled ? 0.55 : 1 }]}>
                    {item.icon ? <Icon name={item.icon} size="sm" color={item.tone ? ink : colors.ink2} /> : null}
                    <Text variant="body" numberOfLines={1} style={{ flex: 1, color: ink }}>{item.label}</Text>
                  </Pressable>
                );
              })}
            </View>
          ))}
        </ScrollView>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  bar: { borderBottomWidth: StyleSheet.hairlineWidth, overflow: 'hidden', zIndex: 10 },
  row: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs, minHeight: 40, paddingLeft: spacing.base, paddingRight: spacing.xs },
  title: { flex: 1, minWidth: 0, fontSize: 15 },
  accessory: { flexDirection: 'row', alignItems: 'center' },
  tabs: { flexDirection: 'row', alignItems: 'center' },
  tab: { width: 36, height: 40, alignItems: 'center', justifyContent: 'center' },
  pill: { width: 32, height: 26, borderRadius: radii.pill, alignItems: 'center', justifyContent: 'center' },
  badge: { position: 'absolute', top: 1, right: -1, minWidth: 15, height: 15, borderRadius: radii.pill, paddingHorizontal: 3, alignItems: 'center', justifyContent: 'center' },
  menu: { position: 'absolute', right: spacing.sm, width: 264, borderRadius: radii.md, overflow: 'hidden' },
  heading: { paddingHorizontal: spacing.md, paddingTop: spacing.sm, paddingBottom: 2 },
  item: { flexDirection: 'row', alignItems: 'center', gap: spacing.md, minHeight: touchTarget, paddingHorizontal: spacing.md },
  rule: { height: StyleSheet.hairlineWidth, marginVertical: spacing.xs },
});
