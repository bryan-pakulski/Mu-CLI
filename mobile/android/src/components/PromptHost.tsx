// Approvals and questions, app-wide (mobile_parity.md §6 Chat).
//
// PromptHost is mounted once (App.tsx). It keeps the pending-prompt queue in sync with the server
// (store/prompts.ts) and, while the chat transcript isn't on screen, shows a "needs you" banner
// that opens the chat, where the prompt is answered inline (components/chat/InlinePrompt.tsx).
// In the chat the inline card is the surface; ChatPromptBanner covers it being scrolled away.

import React from 'react';
import { StyleSheet, View } from 'react-native';
import { navigateTo } from '../navigation/nav';
import { useActivePrompt, usePromptStore, usePromptSync } from '../store/prompts';
import { useTheme } from '../theme/ThemeContext';
import { radii, spacing } from '../theme/tokens';
import { vocabulary } from '../theme/wash.generated';
import { headline, isApproval } from '../ui/approval';
import { GlassBackdrop, useGlass } from '../ui/Glass';
import { Icon } from '../ui/Icon';
import { Pressable } from '../ui/Pressable';
import { Text } from '../ui/Text';

export { ToolApprovalBody } from './chat/InlinePrompt';

/** The "needs you" pill: what is waiting, and a tap to go to it. */
export function NeedsYouBanner({ onPress, testID = 'needs-you-banner', floating }: { onPress: () => void; testID?: string; floating?: boolean }) {
  const { colors } = useTheme();
  const glass = useGlass();
  const { prompt, total } = useActivePrompt();
  if (!prompt) return null;
  const title = isApproval(prompt) ? 'Approval needed' : 'Mucli has a question';
  return (
    <Pressable testID={testID} accessibilityRole="button" accessibilityLabel={`${title}: ${headline(prompt).replace(/`/g, '')}. Review`} haptic="light" onPress={onPress}
      style={[glass.style(2), styles.banner, floating ? styles.floating : null, { backgroundColor: 'transparent', overflow: 'hidden' }]}>
      <GlassBackdrop level={2} testID={`${testID}-glass`} />
      <View pointerEvents="none" style={[StyleSheet.absoluteFill, { backgroundColor: glass.tint('attention') }]} />
      <Text variant="title3" importantForAccessibility="no" style={{ color: colors.attention.ink }}>{vocabulary.status.waiting.glyph}</Text>
      <View style={styles.copy}>
        <Text variant="label">{title}</Text>
        <Text variant="caption" tone="ink3" numberOfLines={1}>{headline(prompt).replace(/`/g, '')}</Text>
      </View>
      {total > 1 ? <Text variant="caption" tone="ink3" numeric>{total} waiting</Text> : null}
      <Icon name="chevron-right" color={colors.ink3} />
    </Pressable>
  );
}

export function PromptHost() {
  usePromptSync();
  const chatVisible = usePromptStore(s => s.chatVisible);
  if (chatVisible) return null;
  return <NeedsYouBanner floating onPress={() => navigateTo('Chat')} />;
}

const styles = StyleSheet.create({
  banner: { minHeight: 64, flexDirection: 'row', alignItems: 'center', gap: spacing.md, borderRadius: radii.lg, paddingHorizontal: spacing.base, borderWidth: 1 },
  floating: { position: 'absolute', bottom: 96, width: '90%', maxWidth: 520, alignSelf: 'center', zIndex: 1000, elevation: 18 },
  copy: { flex: 1 },
});
