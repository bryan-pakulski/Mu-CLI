// The default-mode turn receipt under an assistant message (modes_v2.md §4.1, §5.3 Default):
// one quiet line ("2 files changed · checks passed" / "2 files changed · unverified") that opens
// a sheet with the files and the turn's checks (each opens the shared CheckRun sheet).

import React, { useState } from 'react';
import { StyleSheet, View } from 'react-native';
import type { TurnReceipt } from '../../api/modeState';
import { useTheme } from '../../theme/ThemeContext';
import { modeState } from '../../theme/status';
import { radii, spacing } from '../../theme/tokens';
import { Pressable, Row, Sheet, StatusSign, Text } from '../../ui';
import { receiptLine } from './modeModel';
import { useCheckRunSheet } from './ModeShell';

export function TurnReceiptRow({ receipt, testID }: { receipt: TurnReceipt; testID?: string }) {
  const { colors } = useTheme();
  const [open, setOpen] = useState(false);
  const checks = useCheckRunSheet();
  const line = receiptLine(receipt);
  if (!line) return null;
  const mark = modeState('turn_receipt', receipt.status);
  return (
    <>
      <Pressable onPress={() => setOpen(true)} scale={false} accessibilityRole="button" accessibilityLabel={`Turn receipt: ${line}`} testID={testID}
        style={({ pressed }) => [styles.row, pressed ? { backgroundColor: colors.sunken } : null]}>
        <StatusSign status={mark.status} size={12} />
        <Text variant="caption" tone="ink3" numeric numberOfLines={1} style={styles.flex}>{line}</Text>
      </Pressable>
      <Sheet visible={open} onClose={() => setOpen(false)} title="Turn receipt" testID={testID ? `${testID}-sheet` : undefined}>
        <View style={styles.sheet}>
          <Row status={mark.status} title={line} sub={receipt.nudged ? 'Asked once to verify after the edit.' : undefined} />
          {receipt.files.length ? <Text variant="label" tone="ink2">Files</Text> : null}
          {receipt.files.map(f => <Row key={f} title={<Text variant="monoSm">{f}</Text>} />)}
          {receipt.checks.length ? <Text variant="label" tone="ink2">Checks</Text> : null}
          {receipt.checks.map(c => {
            const cm = modeState('check_run', c.passed ? 'passed' : 'failed');
            return (
              <Row key={c.id} status={cm.status} title={<Text variant="monoSm" numberOfLines={2}>{c.command}</Text>}
                when={cm.label} sub={c.after_edit ? 'after the last edit' : 'before the last edit'}
                onPress={() => { setOpen(false); checks.open(c.id); }} testID={`receipt-check-${c.id}`} />
            );
          })}
        </View>
      </Sheet>
      {checks.element}
    </>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs, minHeight: 32, paddingVertical: spacing.xs, borderRadius: radii.sm },
  flex: { flexShrink: 1 },
  sheet: { gap: spacing.sm },
});
