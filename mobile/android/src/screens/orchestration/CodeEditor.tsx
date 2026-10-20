// A code field for the Studio (plan §6 Studio: editors): a monospace multiline field to edit, a
// highlighted view in the generated `syntax` colours (CodeBlock) to read, and full screen for long
// files. Read-only drafts show only the highlighted view.

import React, { useState } from 'react';
import { StyleSheet, View } from 'react-native';
import { useTheme } from '../../theme/ThemeContext';
import { spacing } from '../../theme/tokens';
import { Button, CodeBlock, Segmented, Sheet, Text, TextField } from '../../ui';

export const LONG_FILE_LINES = 40;

export function CodeEditor({ label, value, onChange, language, editable = true, testID }: {
  label: string; value: string; onChange?: (next: string) => void; language?: string; editable?: boolean; testID?: string;
}) {
  const { colors } = useTheme();
  const [mode, setMode] = useState<'edit' | 'read'>(editable ? 'edit' : 'read');
  const [full, setFull] = useState(false);
  const lines = value.split('\n').length;
  const body = (inSheet: boolean) => (mode === 'edit' && editable ? (
    <TextField value={value} onChangeText={onChange} multiline mono autoCapitalize="none" autoCorrect={false} spellCheck={false}
      accessibilityLabel={label} testID={inSheet ? `${testID}-full` : testID} style={inSheet ? styles.fullField : styles.field} />
  ) : <CodeBlock code={value || ' '} language={language} colors={colors} />);
  return (
    <View style={styles.wrap}>
      <View style={styles.head}>
        <Text variant="label" style={{ flex: 1 }} numberOfLines={1}>{label}</Text>
        {editable ? <Segmented<'edit' | 'read'> accessibilityLabel={`${label} view`} value={mode} onChange={setMode}
          options={[{ value: 'edit', label: 'Edit', testID: `${testID}-edit` }, { value: 'read', label: 'Highlighted', testID: `${testID}-read` }]} /> : null}
        {lines > LONG_FILE_LINES || editable ? <Button size="md" variant="ghost" label="Full screen" onPress={() => setFull(true)} testID={`${testID}-expand`} /> : null}
      </View>
      {body(false)}
      <Sheet visible={full} onClose={() => setFull(false)} title={label} full testID={`${testID}-sheet`}>{body(true)}</Sheet>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { gap: spacing.xs },
  head: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, flexWrap: 'wrap' },
  field: { minHeight: 160, maxHeight: 420, textAlignVertical: 'top' },
  fullField: { minHeight: 480, textAlignVertical: 'top' },
});
