import React, { useState, useCallback } from 'react';
import { FlatList, View, RefreshControl, TouchableOpacity } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useFocusEffect } from '@react-navigation/native';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '../theme/ThemeContext';
import { Text, Card, Skeleton, ErrorState, EmptyState, Badge, Button } from '../components';
import { teacherApi, TeacherState, TeacherModule, TeacherLesson } from '../api/teacher';
import { spacing } from '../theme/tokens';

export function TeacherScreen() {
  const { colors } = useTheme();
  const [state, setState] = useState<TeacherState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [expandedModule, setExpandedModule] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setError(null);
      const res = await teacherApi.getState();
      setState(res);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      setLoading(true);
      load();
    }, [load]),
  );

  if (loading) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
        <View style={{ padding: spacing.base }}>
          <Skeleton height={120} style={{ marginBottom: spacing.sm }} />
          <Skeleton height={80} style={{ marginBottom: spacing.sm }} />
          <Skeleton height={80} />
        </View>
      </SafeAreaView>
    );
  }

  if (error) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
        <ErrorState message={error} onRetry={load} />
      </SafeAreaView>
    );
  }

  if (!state || (!state.active && state.courses.length === 0)) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
        <EmptyState title="No courses" message="No teacher courses available" />
      </SafeAreaView>
    );
  }

  const course = state.course;

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
      <FlatList
        data={course?.modules || []}
        keyExtractor={item => String(item.module_id)}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} />}
        contentContainerStyle={{ padding: spacing.base }}
        ListHeaderComponent={
          course ? (
            <Card style={{ marginBottom: spacing.sm }}>
              <Text variant="base" style={{ fontWeight: '600' }}>{course.subject}</Text>
              <Text variant="xs" style={{ color: colors.textDim, marginTop: 2 }}>
                {course.target_level} · {course.lessons_completed_count}/{course.lesson_total} lessons
              </Text>
              <View style={{ flexDirection: 'row', gap: 6, marginTop: spacing.sm }}>
                <Badge label={course.status} variant={course.status === 'in_progress' ? 'accent' : 'neutral'} />
              </View>
            </Card>
          ) : (
            <Card style={{ marginBottom: spacing.sm }}>
              <Text variant="base" style={{ fontWeight: '600' }}>No active course</Text>
              {state.courses.length > 0 && (
                <>
                  <Text variant="xs" style={{ color: colors.textDim, marginTop: spacing.sm, marginBottom: 4 }}>
                    Available courses ({state.courses.length}):
                  </Text>
                  {state.courses.map(c => (
                    <Text key={c.course_id} variant="sm" style={{ color: colors.text, paddingVertical: 4 }}>
                      {c.subject} · {c.status}
                    </Text>
                  ))}
                </>
              )}
            </Card>
          )
        }
        renderItem={({ item: module }) => (
          <ModuleCard
            module={module}
            expanded={expandedModule === String(module.module_id)}
            onToggle={() => setExpandedModule(prev => prev === String(module.module_id) ? null : String(module.module_id))}
            colors={colors}
          />
        )}
      />
    </SafeAreaView>
  );
}

function ModuleCard({ module, expanded, onToggle, colors }: {
  module: TeacherModule; expanded: boolean; onToggle: () => void; colors: any;
}) {
  return (
    <Card style={{ marginBottom: spacing.sm }}>
      <TouchableOpacity onPress={onToggle} activeOpacity={0.7} style={{ minHeight: 44 }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <View style={{ flex: 1 }}>
            <Text variant="base" style={{ fontWeight: '500' }}>{module.title}</Text>
            <Text variant="xs" style={{ color: colors.textDim, marginTop: 2 }}>
              {module.lessons.filter(l => l.status === 'completed').length}/{module.lessons.length} lessons
            </Text>
          </View>
          {module.status && <Badge label={module.status} variant={module.status === 'completed' ? 'success' : 'neutral'} />}
          <Ionicons name={expanded ? 'chevron-up' : 'chevron-down'} size={20} color={colors.textDim} />
        </View>
      </TouchableOpacity>
      {expanded && (
        <View style={{ marginTop: spacing.sm, paddingTop: spacing.sm, borderTopWidth: 0.5, borderTopColor: colors.border }}>
          {module.lessons.map(lesson => (
            <LessonRow key={lesson.lesson_id} lesson={lesson} colors={colors} />
          ))}
        </View>
      )}
    </Card>
  );
}

function LessonRow({ lesson, colors }: { lesson: TeacherLesson; colors: any }) {
  return (
    <View style={{ paddingVertical: 8, minHeight: 44, borderBottomWidth: 0.5, borderBottomColor: colors.border }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
        <Text variant="sm" style={{ flex: 1, fontWeight: '500' }}>{lesson.title}</Text>
        <Badge label={lesson.status} variant={
          lesson.status === 'completed' ? 'success' :
          lesson.status === 'presenting' || lesson.status === 'in_progress' ? 'accent' :
          'neutral'
        } />
      </View>
      {lesson.lecture_comprehension_pct !== null && (
        <Text variant="xs" style={{ color: colors.textDim, marginTop: 2, fontVariant: ['tabular-nums'] }}>
          Comprehension: {lesson.lecture_comprehension_pct}%
        </Text>
      )}
    </View>
  );
}