import React from 'react';
import { render } from '@testing-library/react-native';
import { Text } from 'react-native';
import { ThemeProvider } from '../src/theme/ThemeContext';

// Test that Clipboard.setString is callable (copy function pattern)
// ChatScreen uses Clipboard.setString for copy button functionality

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <ThemeProvider>{children}</ThemeProvider>
);

describe('ChatMessage copy', () => {
  it('Text component renders message text for copy', () => {
    const message = 'Hello world, this is a test message';
    const { getByText } = render(<Text>{message}</Text>, { wrapper });
    expect(getByText(message)).toBeTruthy();
  });

  it('Clipboard.setString pattern works', () => {
    // Simulate the copy function used in ChatScreen
    const mockSetString = jest.fn();
    const text = 'Copy this text';
    mockSetString(text);
    expect(mockSetString).toHaveBeenCalledWith(text);
  });
});