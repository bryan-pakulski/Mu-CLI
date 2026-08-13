import React from 'react';
import { ScrollView, StyleSheet, TouchableOpacity, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import { StackActions } from '@react-navigation/native';
import { Ionicons } from '@expo/vector-icons';
import { Text } from '../components';
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
        <Text variant="xs" style={[styles.kicker, { color: colors.accent }]}>TOOLS</Text>
        <Text variant="sm" dim style={styles.intro}>{category.description}</Text>
        {category.items.map(item => (
          <TouchableOpacity
            key={item.screen}
            activeOpacity={0.68}
            onPress={() => navigation.dispatch(StackActions.push(item.screen))}
            style={[styles.row, { borderBottomColor: colors.hairline }]}
          >
            <Ionicons name={item.icon} size={19} color={colors.textDim} />
            <View style={styles.copy}>
              <Text variant="base" style={styles.title}>{item.title}</Text>
              <Text variant="sm" dim>{item.description}</Text>
            </View>
            <Ionicons name="chevron-forward" size={18} color={colors.textDim} />
          </TouchableOpacity>
        ))}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safeArea: { flex: 1 },
  content: { paddingBottom: 40 },
  kicker: { fontWeight: '700', letterSpacing: 1.25, marginBottom: 7 },
  intro: { marginBottom: 22, maxWidth: 340 },
  row: { flexDirection: 'row', alignItems: 'center', minHeight: 82, paddingVertical: 14, borderBottomWidth: StyleSheet.hairlineWidth },
  copy: { flex: 1, marginHorizontal: 14 },
  title: { fontWeight: '600', marginBottom: 2 },
});
