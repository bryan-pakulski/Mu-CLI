import React from 'react';
import { render, fireEvent } from '@testing-library/react-native';
import { Button } from '../src/components/Button';
import { ThemeProvider } from '../src/theme/ThemeContext';

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <ThemeProvider>{children}</ThemeProvider>
);

describe('Button component', () => {
  it('renders title text', () => {
    const { getByText } = render(<Button title="Click me" onPress={() => {}} />, { wrapper });
    expect(getByText('Click me')).toBeTruthy();
  });

  it('calls onPress when tapped', () => {
    const onPress = jest.fn();
    const { getByText } = render(<Button title="Tap" onPress={onPress} />, { wrapper });
    fireEvent.press(getByText('Tap'));
    expect(onPress).toHaveBeenCalledTimes(1);
  });

  it('does not call onPress when disabled', () => {
    const onPress = jest.fn();
    const { getByText } = render(<Button title="Tap" onPress={onPress} disabled />, { wrapper });
    fireEvent.press(getByText('Tap'));
    expect(onPress).not.toHaveBeenCalled();
  });
});