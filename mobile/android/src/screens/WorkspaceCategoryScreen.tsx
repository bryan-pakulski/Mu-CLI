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
import { fontWeights, spacing } from '../theme/tokens';

export type WorkspaceCategoryScreenProps = NativeStackScreenProps<RootStackParamList, 'WorkspaceCategory'>;

export function WorkspaceCategoryScreen({ navigation, route }: WorkspaceCategoryScreenProps) {
  const { colors, spacing } = useTheme();
  const category = getWorkspaceCategory(route.params.categoryId);

  return (
    <SafeAreaView edges={['bottom']} style={[styles.safeArea, { backgroundColor: colors.canvas }]}>
      <ScrollView contentContainerStyle={[styles.content, { padding: spacing.base }]}>
        <Text variant="xs" style={[styles.kicker, { color: colors.ink3 }]}>Tools</Text>
        <Text variant="sm" dim style={[styles.intro, { borderBottomColor: colors.hairline }]}>{category.description}</Text>
        {category.items.map(item => (
          <TouchableOpacity
            key={item.screen}
            activeOpacity={0.68}
            onPress={() => navigation.dispatch(StackActions.push(item.screen))}
            style={styles.row}
          >
            <Ionicons name={item.icon} size={19} color={colors.ink3} />
            <View style={styles.copy}>
              <Text variant="base" style={styles.title}>{item.title}</Text>
              <Text variant="sm" dim>{item.description}</Text>
            </View>
            <Ionicons name="chevron-forward" size={18} color={colors.ink3} />
          </TouchableOpacity>
        ))}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safeArea: { flex: 1 },
  content: { paddingBottom: spacing['2xl'] },
  kicker: { fontWeight: fontWeights.semibold, marginBottom: spacing.sm },
  intro: { marginBottom: spacing.sm, paddingBottom: spacing.base, borderBottomWidth: 1 },
  row: { flexDirection: 'row', alignItems: 'center', minHeight: 72, paddingVertical: spacing.base },
  copy: { flex: 1, marginHorizontal: spacing.md },
  title: { fontWeight: fontWeights.semibold, marginBottom: 2 },
});
