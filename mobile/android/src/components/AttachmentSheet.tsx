
import React, { useCallback, useEffect, useState } from 'react';
import { Alert, StyleSheet, View } from 'react-native';
import * as DocumentPicker from 'expo-document-picker';
import { attachmentsApi, type AttachmentDescriptor } from '../api/attachments';
import { useTheme } from '../theme/ThemeContext';
import { fontWeights, radii, spacing } from '../theme/tokens';
import { Button, Pressable, Sheet, Text, Icon, InkDrop } from '../ui';

interface Props {
  visible: boolean;
  sessionName: string;
  selected: AttachmentDescriptor[];
  onSelectedChange: (items: AttachmentDescriptor[]) => void;
  onClose: () => void;
}

function formatSize(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export function AttachmentSheet({ visible, sessionName, selected, onSelectedChange, onClose }: Props) {
  const { colors } = useTheme();
  const [items, setItems] = useState<AttachmentDescriptor[]>([]);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const selectedIds = new Set(selected.map(item => item.attachment_id));

  const load = useCallback(async () => {
    if (!sessionName) return;
    setLoading(true);
    try {
      const response = await attachmentsApi.list(sessionName);
      setItems(response.attachments || []);
    } catch (error) {
      Alert.alert('Could not load attachments', String(error));
    } finally {
      setLoading(false);
    }
  }, [sessionName]);

  useEffect(() => { if (visible) void load(); }, [load, visible]);

  const toggle = (item: AttachmentDescriptor) => {
    if (selectedIds.has(item.attachment_id)) {
      onSelectedChange(selected.filter(value => value.attachment_id !== item.attachment_id));
    } else {
      onSelectedChange([...selected, item]);
    }
  };

  const pick = async () => {
    const result = await DocumentPicker.getDocumentAsync({
      type: '*/*',
      multiple: true,
      copyToCacheDirectory: true,
    });
    if (result.canceled) return;
    setUploading(true);
    try {
      const uploaded: AttachmentDescriptor[] = [];
      for (const asset of result.assets) {
        uploaded.push(await attachmentsApi.upload(sessionName, asset));
      }
      await load();
      const byId = new Map(selected.map(item => [item.attachment_id, item]));
      uploaded.forEach(item => byId.set(item.attachment_id, item));
      onSelectedChange(Array.from(byId.values()));
    } catch (error) {
      Alert.alert('Upload failed', String(error));
    } finally {
      setUploading(false);
    }
  };

  const remove = (item: AttachmentDescriptor) => {
    Alert.alert('Delete attachment?', `Remove “${item.name}” from this session?`, [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Delete',
        style: 'destructive',
        onPress: async () => {
          try {
            await attachmentsApi.remove(sessionName, item.attachment_id);
            onSelectedChange(selected.filter(value => value.attachment_id !== item.attachment_id));
            await load();
          } catch (error) {
            Alert.alert('Delete failed', String(error));
          }
        },
      },
    ]);
  };

  return (
    <Sheet visible={visible} onClose={onClose} title="Attachments">
      <View style={styles.header}>
        <Text variant="lg" style={{ fontWeight: fontWeights.semibold }}>Attachments</Text>
        <Pressable onPress={onClose} accessibilityLabel="Close attachments" hitSlop={{ top: spacing.md, bottom: spacing.md, left: spacing.md, right: spacing.md }}>
          <Icon name="x" size="md" color={colors.ink3} />
        </Pressable>
      </View>
      <View style={styles.toolbar}>
        <Button title={uploading ? 'Uploading…' : 'Upload documents'} onPress={pick} disabled={uploading} />
        {uploading ? <InkDrop /> : null}
      </View>
      <Text variant="xs" style={{ color: colors.ink3, marginBottom: spacing.sm }}>
        Uploaded files are stored with this session. Select files to attach them to the next message.
      </Text>
      {loading ? <InkDrop /> : (
        <View style={styles.list}>
          {items.length === 0 ? <Text variant="sm" dim>No uploaded documents</Text> : null}
          {items.map(item => {
            const active = selectedIds.has(item.attachment_id);
            return (
              <Pressable
                key={item.attachment_id}
                onPress={() => toggle(item)}
                style={[styles.row, { backgroundColor: active ? colors.accent.wash : colors.surface, borderWidth: 1, borderColor: active ? colors.accent.ink : colors.hairline }]}
              >
                <Icon name={active ? 'square-check' : 'file'} size="md" color={active ? colors.accent.ink : colors.ink3} />
                <View style={styles.copy}>
                  <Text variant="sm" numberOfLines={1} style={{ fontWeight: fontWeights.semibold }}>{item.name}</Text>
                  <Text variant="xs" dim>{formatSize(item.size)} · {item.mime_type}</Text>
                </View>
                <Pressable accessibilityLabel="Remove attachment" onPress={() => remove(item)} hitSlop={{ top: spacing.md, bottom: spacing.md, left: spacing.md, right: spacing.md }}>
                  <Icon name="trash-2" size="md" color={colors.ink3} />
                </Pressable>
              </Pressable>
            );
          })}
        </View>
      )}
    </Sheet>
  );
}

const styles = StyleSheet.create({
  header: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: spacing.md },
  toolbar: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, marginBottom: spacing.sm },
  list: { maxHeight: 420 },
  row: { minHeight: 64, flexDirection: 'row', alignItems: 'center', gap: spacing.md, borderRadius: radii.sm, paddingHorizontal: spacing.md, marginBottom: spacing.sm },
  copy: { flex: 1, minWidth: 0 },
});
