import React, { useEffect, useRef, useState } from 'react';
import { StyleSheet, View } from 'react-native';
import { sessionsApi } from '../api/sessions';
import { useTheme } from '../theme/ThemeContext';
import { fontSizes, radii, spacing, touchTarget, typography } from '../theme/tokens';
import { Pressable, Text, Icon, InkDrop, TextField } from '../ui';

export type WorkspacePathFieldProps = {
  value: string;
  onChangeText: (value: string) => void;
  placeholder?: string;
  autoFocus?: boolean;
  disabled?: boolean;
  /** The caller creates a missing folder (New session): say so instead of asking for an existing one. */
  createMissing?: boolean;
};

export function WorkspacePathField({
  value,
  onChangeText,
  placeholder = '/home/user/dev/project',
  autoFocus = false,
  disabled = false,
  createMissing = false,
}: WorkspacePathFieldProps) {
  const { colors } = useTheme();
  const [focused, setFocused] = useState(false);
  const [loading, setLoading] = useState(false);
  const [exists, setExists] = useState(false);
  const [resolvedPath, setResolvedPath] = useState('');
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const requestId = useRef(0);

  useEffect(() => {
    if (!focused || disabled) return;
    const currentRequest = ++requestId.current;
    const timer = setTimeout(async () => {
      setLoading(true);
      try {
        const response = await sessionsApi.suggestWorkspaces(value, 10);
        if (requestId.current !== currentRequest) return;
        setSuggestions(response.suggestions || []);
        setExists(Boolean(response.exists));
        setResolvedPath(response.resolved_path || '');
      } catch {
        if (requestId.current !== currentRequest) return;
        setSuggestions([]);
        setExists(false);
        setResolvedPath('');
      } finally {
        if (requestId.current === currentRequest) setLoading(false);
      }
    }, 180);
    return () => clearTimeout(timer);
  }, [disabled, focused, value]);

  const choose = (path: string) => {
    onChangeText(path);
    setExists(true);
    setResolvedPath(path);
    setSuggestions([]);
  };

  return (
    <View>
      <View style={styles.inputShell}>
        <Icon name="folder" size="md" color={colors.ink3} />
        <TextField
          value={value}
          onChangeText={onChangeText}
          onFocus={() => setFocused(true)}
          onBlur={() => setTimeout(() => setFocused(false), 140)}
          autoFocus={autoFocus}
          editable={!disabled}
          autoCapitalize="none"
          autoCorrect={false}
          placeholder={placeholder}
          style={styles.input}
        />
        {loading ? (
          <InkDrop />
        ) : value.trim() && exists ? (
          <Icon name="circle-check" size="md" color={colors.ok.ink} />
        ) : null}
      </View>

      {focused && suggestions.length > 0 ? (
        <View style={[styles.suggestions, { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.hairline }]}>
          {suggestions.map((path, index) => (
            <Pressable
              key={path}
              onPress={() => choose(path)}
              activeOpacity={0.65}
              style={[
                styles.suggestion,
              ]}
            >
              <Icon name="folder" size="sm" color={colors.ink3} />
              <Text variant="xs" style={styles.suggestionText} numberOfLines={1} ellipsizeMode="middle">
                {path}
              </Text>
              <Icon name="corner-down-left" size="sm" color={colors.ink3} />
            </Pressable>
          ))}
        </View>
      ) : null}

      {value.trim() && !loading ? (
        <Text variant="xs" dim style={styles.statusText} numberOfLines={1} ellipsizeMode="middle">
          {exists ? `Folder found: ${resolvedPath}`
            : createMissing ? `New folder: ${resolvedPath || value.trim()} is created with the session.`
            : 'Enter an existing folder on the MuCLI host.'}
        </Text>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  inputShell: {
    minHeight: 48,
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
  },
  input: { flex: 1 },
  suggestions: {
    marginTop: spacing.sm,
    borderRadius: radii.sm,
    overflow: 'hidden',
  },
  suggestion: {
    minHeight: touchTarget,
    paddingHorizontal: spacing.md,
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
  },
  suggestionText: { flex: 1 },
  statusText: { marginTop: spacing.sm, lineHeight: typography.xs.lineHeight },
});
