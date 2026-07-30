
import React, { useCallback, useEffect, useState } from 'react';
import { ActivityIndicator, Alert, StyleSheet, TouchableOpacity, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import * as DocumentPicker from 'expo-document-picker';
import { attachmentsApi, type AttachmentDescriptor } from '../api/attachments';
import { useTheme } from '../theme/ThemeContext';
import { BottomSheet } from './BottomSheet';
import { Button } from './Button';
import { Text } from './Text';

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
    <BottomSheet visible={visible} onClose={onClose} title="Attachments">
      <View style={styles.header}>
        <Text variant="lg" style={{ fontWeight: '700' }}>Attachments</Text>
        <TouchableOpacity onPress={onClose} accessibilityLabel="Close attachments">
          <Ionicons name="close" size={22} color={colors.textDim} />
        </TouchableOpacity>
      </View>
      <View style={styles.toolbar}>
        <Button title={uploading ? 'Uploading…' : 'Upload documents'} onPress={pick} disabled={uploading} />
        {uploading ? <ActivityIndicator color={colors.accent} /> : null}
      </View>
      <Text variant="xs" style={{ color: colors.textDim, marginBottom: 10 }}>
        Uploaded files are stored with this session. Select files to attach them to the next message.
      </Text>
      {loading ? <ActivityIndicator color={colors.accent} /> : (
        <View style={styles.list}>
          {items.length === 0 ? <Text variant="sm" dim>No uploaded documents</Text> : null}
          {items.map(item => {
            const active = selectedIds.has(item.attachment_id);
            return (
              <TouchableOpacity
                key={item.attachment_id}
                onPress={() => toggle(item)}
                style={[styles.row, { borderColor: active ? colors.accent : colors.border, backgroundColor: active ? colors.accentSoft : colors.bgLift }]}
              >
                <Ionicons name={active ? 'checkbox' : 'document-outline'} size={20} color={active ? colors.accent : colors.textDim} />
                <View style={styles.copy}>
                  <Text variant="sm" numberOfLines={1} style={{ fontWeight: '600' }}>{item.name}</Text>
                  <Text variant="xs" dim>{formatSize(item.size)} · {item.mime_type}</Text>
                </View>
                <TouchableOpacity onPress={() => remove(item)} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
                  <Ionicons name="trash-outline" size={18} color={colors.textDim} />
                </TouchableOpacity>
              </TouchableOpacity>
            );
          })}
        </View>
      )}
    </BottomSheet>
  );
}

const styles = StyleSheet.create({
  header: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 },
  toolbar: { flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 10 },
  list: { maxHeight: 420 },
  row: { minHeight: 62, flexDirection: 'row', alignItems: 'center', gap: 11, borderWidth: StyleSheet.hairlineWidth, borderRadius: 14, paddingHorizontal: 12, marginBottom: 8 },
  copy: { flex: 1, minWidth: 0 },
});
