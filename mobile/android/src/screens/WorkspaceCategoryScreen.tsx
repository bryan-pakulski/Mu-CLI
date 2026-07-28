import React from 'react';
import { ScrollView, StyleSheet, TouchableOpacity, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import { StackActions } from '@react-navigation/native';
import { Ionicons } from '@expo/vector-icons';
import { Card, Text } from '../components';
import { useTheme } from '../theme/ThemeContext';
import type { RootStackParamList } from '../navigation/AppNavigator';
import { getWorkspaceCategory } from '../navigation/workspace';

export type WorkspaceCategoryScreenProps = NativeStackScreenProps<RootStackParamList, 'WorkspaceCategory'>;

export function WorkspaceCategoryScreen({ navigation, route }: WorkspaceCategoryScreenProps) {
  const { colors, spacing } = useTheme();
  const category = getWorkspaceCategory(route.params.categoryId);

  return (
    <SafeAreaView edges={['bottom']} style={[styles.safeArea, { backgroundColor: colors.bg }]}>
      <ScrollView contentContainerStyle={[styles.content, { padding: spacing.base }]}>
        <Text variant="sm" dim style={styles.intro}>{category.description}</Text>
        <Card style={styles.listCard}>
          {category.items.map((item, index) => (
            <TouchableOpacity
              key={item.screen}
              activeOpacity={0.68}
              onPress={() => navigation.dispatch(StackActions.push(item.screen))}
              style={[
                styles.row,
                index < category.items.length - 1 && { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border },
              ]}
            >
              <View style={[styles.iconWrap, { backgroundColor: colors.bgHover }]}>
                <Ionicons name={item.icon} size={20} color={colors.text} />
              </View>
              <View style={styles.copy}>
                <Text variant="base" style={styles.title}>{item.title}</Text>
                <Text variant="sm" dim>{item.description}</Text>
              </View>
              <Ionicons name="chevron-forward" size={19} color={colors.textDim} />
            </TouchableOpacity>
          ))}
        </Card>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safeArea: { flex: 1 },
  content: { paddingBottom: 40 },
  intro: { marginBottom: 18, maxWidth: 340 },
  listCard: { padding: 0, overflow: 'hidden' },
  row: { flexDirection: 'row', alignItems: 'center', minHeight: 78, paddingHorizontal: 16, paddingVertical: 12 },
  iconWrap: { width: 42, height: 42, borderRadius: 14, alignItems: 'center', justifyContent: 'center' },
  copy: { flex: 1, marginHorizontal: 14 },
  title: { fontWeight: '600', marginBottom: 2 },
});
