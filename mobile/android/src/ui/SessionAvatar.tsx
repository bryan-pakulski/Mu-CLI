// Session avatar (G§9.20): a pigment tile with the session's monogram and a type badge, the
// same as the web's .w-avatar (static/js/app.js sessionAvatarMarkup). The pigment is a stable
// hash of the session name, so a session keeps its colour on every surface.

import React from 'react';
import { View, StyleSheet } from 'react-native';
import { useTheme } from '../theme/ThemeContext';
import { fontWeights, radii, type PigmentName } from '../theme/tokens';
import { Text } from './Text';
import { Icon, type IconName } from './Icon';

export const AVATAR_PIGMENTS: readonly PigmentName[] = ['lagoon', 'meadow', 'sun', 'apricot', 'rose', 'lilac', 'sky', 'plum'];
const BADGE: Record<string, IconName> = { workspace: 'folder', container: 'box', chat: 'message-circle' };

/** "Harvest-House" → "HH", "rsq-cloud" → "RC", "codex" → "Co", "myApp" → "MA". */
export function sessionMonogram(name: string): string {
  const words = String(name || '').trim().replace(/([a-z])([A-Z])/g, '$1 $2').split(/[^A-Za-z0-9]+/).filter(Boolean);
  if (!words.length) return '?';
  if (words.length === 1) return words[0][0].toUpperCase() + (words[0][1] || '').toLowerCase();
  return (words[0][0] + words[1][0]).toUpperCase();
}

/** Same hash as the web (sessionPigment), so colours match across surfaces. */
export function sessionPigment(name: string): PigmentName {
  let hash = 0;
  for (const ch of String(name || '')) hash = (hash * 31 + (ch.codePointAt(0) || 0)) >>> 0;
  return AVATAR_PIGMENTS[hash % AVATAR_PIGMENTS.length];
}

export function SessionAvatar({ name, type, size = 32, dimmed = false }: { name: string; type?: string; size?: number; dimmed?: boolean }) {
  const { colors } = useTheme();
  const tone = colors.pigment[sessionPigment(name)];
  const kind = type === 'container' || type === 'chat' ? type : 'workspace';
  const badge = Math.round(size * 0.5);
  return (
    <View
      accessible={false}
      importantForAccessibility="no-hide-descendants"
      style={[styles.tile, { width: size, height: size, borderRadius: radii.sm, backgroundColor: tone.wash, borderColor: tone.pigment, opacity: dimmed ? 0.78 : 1 }]}
    >
      <Text style={{ color: tone.ink, fontSize: Math.round(size * 0.42), lineHeight: Math.round(size * 0.5), fontWeight: fontWeights.semibold }}>
        {sessionMonogram(name)}
      </Text>
      <View style={[styles.badge, { width: badge, height: badge, borderRadius: badge / 2, backgroundColor: colors.raised, borderColor: colors.canvas }]}>
        <View style={{ transform: [{ scale: (badge * 0.7) / 16 }] }}>
          <Icon name={BADGE[kind]} size="sm" color={tone.ink} />
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  tile: { alignItems: 'center', justifyContent: 'center', borderWidth: StyleSheet.hairlineWidth },
  badge: { position: 'absolute', right: -4, bottom: -4, alignItems: 'center', justifyContent: 'center', borderWidth: 1.5, overflow: 'hidden' },
});
