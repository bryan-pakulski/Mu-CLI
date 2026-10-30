// Settings (documentation/plans/mobile_parity.md §6 Settings), the web's settings page on a phone:
//   Appearance  theme cards (Wash and the palettes, each with a light and a dark side), scenery
//               (Wash only: Classic or a watercolour pack), light or dark, glass strength (Wash
//               only), Reduce decoration
//   Devices     this phone's pairing (the computer lists and revokes devices)
//   Areas       each area keeps its own settings beside its work
// Session-level settings (mode, model, effort, plan, auto-approve, variables) live with the
// conversation: the composer foot and the Chat ⋯ menu.

import React, { useEffect, useState } from 'react';
import { Image, ScrollView, StyleSheet, View } from 'react-native';
import { useNavigation } from '@react-navigation/native';
import { devicesApi, type PairedDevice } from '../api/devices';
import { useConnectionStore } from '../store/connection';
import { TEXT_SCALE_RANGE, useTheme, type ThemePreference } from '../theme/ThemeContext';
import { PALETTES, SCENERY, paletteLabel, paletteNote, radii, sceneryAccent, sceneryLabel, sceneryNote, spacing, washColors, type PaletteName, type SceneryName } from '../theme/tokens';
import { PageHeader, Pressable, Row, Rows, Segmented, Slider, SwitchRow, Text } from '../ui';
import { washArt } from '../ui/WashBackground';

const GLASS_STOPS = [0, 20, 40, 60, 80, 100].map(v => ({ value: String(v), label: v === 0 ? 'Off' : `${v}%` }));
// Text size stops from tokens.json density.text_scale (85–120 in steps of 5).
const TEXT_STOPS: Array<{ value: string; label: string }> = [];
for (let v = TEXT_SCALE_RANGE.min; v <= TEXT_SCALE_RANGE.max; v += TEXT_SCALE_RANGE.step) {
  TEXT_STOPS.push({ value: String(v), label: `${v}%` });
}

export function SettingsScreen() {
  const navigation = useNavigation<any>();
  const { colors, preference, setPreference, reduceDecoration, setReduceDecoration, palette, setPalette, sceneryChoice, setScenery, glass, setGlass, textScale, setTextScale } = useTheme();
  const { baseUrl, isConnected, pairingRequired } = useConnectionStore();
  const [me, setMe] = useState<PairedDevice | null>(null);
  const server = baseUrl.replace(/^https?:\/\//, '');

  useEffect(() => {
    let cancelled = false;
    if (!isConnected || pairingRequired) { setMe(null); return () => { cancelled = true; }; }
    devicesApi.whoami(baseUrl).then(d => { if (!cancelled) setMe(d); }).catch(() => { if (!cancelled) setMe(null); });
    return () => { cancelled = true; };
  }, [baseUrl, isConnected, pairingRequired]);

  // The nearest stop, so a web-set 65% still shows a selection.
  const glassStop = String(Math.round(glass / 20) * 20);
  const go = (name: string, params?: object) => () => navigation.navigate(name, params);

  return (
    <View style={{ flex: 1 }} testID="settings-screen">
      <PageHeader title="Settings" back={navigation.canGoBack?.() ? { label: 'More', onPress: () => navigation.goBack() } : undefined} state={[{ text: `${paletteLabel(palette)} theme`, tone: '' }, { text: isConnected ? `connected to ${server}` : 'not connected', tone: isConnected ? 'quiet' : 'attention' }]} />
      <ScrollView contentContainerStyle={styles.content}>
        <Section title="Appearance" lede="How μcli looks on this phone.">
          <View style={styles.themes} accessibilityRole="radiogroup" accessibilityLabel="Theme">
            {([null, ...PALETTES] as Array<PaletteName | null>).map(name => (
              <ThemeCard key={name ?? 'wash'} name={name} selected={palette === name} onPress={() => setPalette(name)} />
            ))}
          </View>
          {palette == null ? (
            <View style={styles.setting} testID="scenery-setting">
              <Text variant="label">Scenery</Text>
              <Text variant="caption" tone="ink3">The watercolour behind the glass. Classic gives each place its own; a pack paints them all and brings its colour.</Text>
              <View style={styles.themes} accessibilityRole="radiogroup" accessibilityLabel="Scenery">
                {([null, ...SCENERY] as Array<SceneryName | null>).map(name => (
                  <SceneryCard key={name ?? 'classic'} name={name} selected={sceneryChoice === name} onPress={() => setScenery(name)} />
                ))}
              </View>
            </View>
          ) : null}
          <View style={styles.setting}>
            <Text variant="label">Light or dark</Text>
            <Text variant="caption" tone="ink3">System follows the phone. Every theme has both.</Text>
            <Segmented<ThemePreference>
              accessibilityLabel="Light or dark"
              testID="theme-preference"
              value={preference}
              onChange={setPreference}
              options={[
                { value: 'system', label: 'System', testID: 'theme-system' },
                { value: 'paper', label: 'Light', testID: 'theme-paper' },
                { value: 'dusk', label: 'Dark', testID: 'theme-dusk' },
              ]}
            />
          </View>
          {palette == null ? (
            <View style={styles.setting} testID="glass-setting">
              <Text variant="label">Glass</Text>
              <Text variant="caption" tone="ink3">How much of the watercolour shows through the page. Text stays readable at every setting.</Text>
              <Slider stops={GLASS_STOPS} value={glassStop} onChange={v => setGlass(Number(v))} accessibilityLabel="Glass" disabled={reduceDecoration} />
            </View>
          ) : null}
          <View style={styles.setting} testID="text-scale-setting">
            <Text variant="label">Text size</Text>
            <Text variant="caption" tone="ink3">Smaller fits more on screen. Applies on top of the phone's own font size.</Text>
            <Slider stops={TEXT_STOPS} value={String(textScale)} onChange={v => setTextScale(Number(v))} accessibilityLabel="Text size" />
          </View>
          <SwitchRow label="Reduce decoration" hint="A flat colour instead of the watercolour around the page."
            value={reduceDecoration} onValueChange={setReduceDecoration} testID="reduce-decoration" />
        </Section>

        <Section title="Devices" lede="Anything not on the computer running μcli signs in by pairing.">
          <Rows>
            <Row testID="settings-pair" status={pairingRequired ? 'waiting' : me ? 'done' : undefined}
              title={me ? `This phone: ${me.name}` : pairingRequired ? 'This phone needs pairing' : 'Pair with mucli'}
              sub={me ? 'Signed in. Sign out here, or revoke it from Settings → Devices on the computer.' : 'Scan the code from Settings → Devices on the computer.'}
              onPress={go('Pair')} />
          </Rows>
        </Section>

        <Section title="Areas" lede="Each area keeps its own settings beside its work.">
          <Rows>
            <Row testID="area-chat" title="Chat and workspace" sub="Provider, model, tools and the workspace for the open session" onPress={go('Tabs', { screen: 'Chat', params: { openSettings: Date.now() } })} />
            <Row testID="area-work" title="Work" sub="Job rules, agents, checks and environments" onPress={go('JobSettings')} />
            <Row testID="area-orchestration" title="Orchestration" sub="Access policy, schedules, secrets and the network" onPress={go('Tabs', { screen: 'Orchestration', params: { tab: 'settings' } })} />
            <Row testID="area-pricing" title="Model pricing" sub="Prices used for cost estimates" onPress={go('Tabs', { screen: 'Insights', params: { tab: 'costs' } })} />
            <Row testID="area-connection" title="Connection" sub={server} onPress={go('Connection')} />
            <Row testID="area-providers" title="Provider and model" sub="The model for this session" onPress={go('Providers')} />
          </Rows>
        </Section>
        <Text variant="caption" tone="ink3" style={{ paddingHorizontal: spacing.xs }}>{colors.name === 'dusk' ? 'Dark' : 'Light'} · {paletteNote(palette)}</Text>
      </ScrollView>
    </View>
  );
}

function Section({ title, lede, children }: { title: string; lede: string; children: React.ReactNode }) {
  return (
    <View style={styles.section}>
      <View style={styles.sectionHead}>
        <Text variant="title3" accessibilityRole="header">{title}</Text>
        <Text variant="caption" tone="ink3">{lede}</Text>
      </View>
      {children}
    </View>
  );
}

/** A theme card: both sides (light and dark) as a tiny page, the label and the note (web .w-themecard). */
function ThemeCard({ name, selected, onPress }: { name: PaletteName | null; selected: boolean; onPress: () => void }) {
  const { colors } = useTheme();
  const label = paletteLabel(name);
  return (
    <Pressable onPress={onPress} scale={false} accessibilityRole="radio" accessibilityLabel={`${label} theme`} accessibilityHint={paletteNote(name)}
      accessibilityState={{ selected, checked: selected }} testID={`palette-${name ?? 'wash'}`}
      style={[styles.card, { borderColor: selected ? colors.ink : colors.hairline, borderWidth: selected ? 2 : 1, backgroundColor: colors.surface }]}>
      <View style={styles.sides} importantForAccessibility="no-hide-descendants">
        {(['paper', 'dusk'] as const).map(side => {
          const c = washColors(side, name);
          return (
            <View key={side} style={[styles.side, { backgroundColor: c.canvas, borderColor: c.hairline }]}>
              <View style={[styles.bar, { backgroundColor: c.surface }]} />
              <View style={[styles.line, { width: '80%', backgroundColor: c.ink }]} />
              <View style={[styles.line, { width: '50%', backgroundColor: c.ink3 }]} />
              <View style={styles.dots}>
                {[c.accent, c.ok, c.attention, c.danger, c.plan].map((t, i) => <View key={i} style={[styles.dot, { backgroundColor: t.pigment }]} />)}
              </View>
            </View>
          );
        })}
      </View>
      <Text variant="label" numberOfLines={1}>{label}</Text>
    </Pressable>
  );
}

/** A scenery choice: the pack's light and dark paintings (Classic shows dawn), its accent and name. */
function SceneryCard({ name, selected, onPress }: { name: SceneryName | null; selected: boolean; onPress: () => void }) {
  const { colors } = useTheme();
  const label = sceneryLabel(name);
  const scene = name ?? 'dawn';
  return (
    <Pressable onPress={onPress} scale={false} accessibilityRole="radio" accessibilityLabel={`${label} scenery`} accessibilityHint={sceneryNote(name)}
      accessibilityState={{ selected, checked: selected }} testID={`scenery-${name ?? 'classic'}`}
      style={[styles.card, { borderColor: selected ? colors.ink : colors.hairline, borderWidth: selected ? 2 : 1, backgroundColor: colors.surface }]}>
      <View style={styles.sides} importantForAccessibility="no-hide-descendants">
        {(['paper', 'dusk'] as const).map(side => (
          <Image key={side} source={washArt(scene, side)} resizeMode="cover" style={[styles.side, styles.thumb, { borderColor: colors.hairline }]} />
        ))}
      </View>
      <View style={styles.sceneryLabel}>
        <View style={[styles.dot, styles.sceneryDot, { backgroundColor: name ? colors.pigment[sceneryAccent(name)].pigment : colors.accent.pigment }]} />
        <Text variant="label" numberOfLines={1}>{label}</Text>
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  thumb: { padding: 0, overflow: 'hidden' },
  sceneryLabel: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs },
  sceneryDot: { width: 7, height: 7, borderRadius: 4 },
  content: { padding: spacing.base, paddingBottom: spacing['2xl'], gap: spacing.xl },
  section: { gap: spacing.md },
  sectionHead: { gap: spacing.xs },
  setting: { gap: spacing.xs },
  themes: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  card: { width: '31%', minWidth: 100, flexGrow: 1, borderRadius: radii.md, padding: spacing.sm, gap: spacing.xs },
  sides: { flexDirection: 'row', gap: spacing.xs },
  side: { flex: 1, height: 52, borderRadius: radii.sm, borderWidth: 1, padding: spacing.xs, gap: 3 },
  bar: { height: 8, borderRadius: radii.xs },
  line: { height: 3, borderRadius: radii.xs },
  dots: { flexDirection: 'row', gap: 2, marginTop: 'auto' },
  dot: { width: 5, height: 5, borderRadius: 3 },
});
