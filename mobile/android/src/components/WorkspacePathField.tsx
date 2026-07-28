import React, { useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  StyleSheet,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { sessionsApi } from '../api/sessions';
import { useTheme } from '../theme/ThemeContext';
import { Text } from './Text';

export type WorkspacePathFieldProps = {
  value: string;
  onChangeText: (value: string) => void;
  placeholder?: string;
  autoFocus?: boolean;
  disabled?: boolean;
};

export function WorkspacePathField({
  value,
  onChangeText,
  placeholder = '/home/user/dev/project',
  autoFocus = false,
  disabled = false,
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
      <View style={[styles.inputShell, { backgroundColor: colors.bgLift }]}>
        <Ionicons name="folder-outline" size={18} color={colors.textDim} />
        <TextInput
          value={value}
          onChangeText={onChangeText}
          onFocus={() => setFocused(true)}
          onBlur={() => setTimeout(() => setFocused(false), 140)}
          autoFocus={autoFocus}
          editable={!disabled}
          autoCapitalize="none"
          autoCorrect={false}
          placeholder={placeholder}
          placeholderTextColor={colors.textDim}
          style={[styles.input, { color: colors.text }]}
        />
        {loading ? (
          <ActivityIndicator size="small" color={colors.accent} />
        ) : value.trim() && exists ? (
          <Ionicons name="checkmark-circle" size={18} color={colors.success} />
        ) : null}
      </View>

      {focused && suggestions.length > 0 ? (
        <View style={[styles.suggestions, { backgroundColor: colors.bgLift, borderColor: colors.border }]}>
          {suggestions.map((path, index) => (
            <TouchableOpacity
              key={path}
              onPress={() => choose(path)}
              activeOpacity={0.65}
              style={[
                styles.suggestion,
                index < suggestions.length - 1 && { borderBottomColor: colors.border, borderBottomWidth: StyleSheet.hairlineWidth },
              ]}
            >
              <Ionicons name="folder" size={16} color={colors.textDim} />
              <Text variant="xs" style={styles.suggestionText} numberOfLines={1} ellipsizeMode="middle">
                {path}
              </Text>
              <Ionicons name="return-down-back" size={15} color={colors.textDim} />
            </TouchableOpacity>
          ))}
        </View>
      ) : null}

      {value.trim() && !loading ? (
        <Text variant="xs" dim style={styles.statusText} numberOfLines={1} ellipsizeMode="middle">
          {exists ? `Folder found: ${resolvedPath}` : 'Enter an existing folder on the MuCLI host.'}
        </Text>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  inputShell: {
    minHeight: 48,
    borderRadius: 15,
    paddingHorizontal: 13,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 9,
  },
  input: {
    flex: 1,
    minHeight: 48,
    paddingVertical: 11,
    fontSize: 15,
  },
  suggestions: {
    marginTop: 7,
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: 14,
    overflow: 'hidden',
  },
  suggestion: {
    minHeight: 44,
    paddingHorizontal: 12,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 9,
  },
  suggestionText: { flex: 1 },
  statusText: { marginTop: 7, lineHeight: 17 },
});
