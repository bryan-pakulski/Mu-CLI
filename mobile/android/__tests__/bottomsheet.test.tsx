import React from 'react';
import { Text as RNText } from 'react-native';
import { render } from '@testing-library/react-native';
import { BottomSheet } from '../src/components/BottomSheet';
import { ThemeProvider } from '../src/theme/ThemeContext';

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <ThemeProvider>{children}</ThemeProvider>
);

describe('BottomSheet component', () => {
  it('renders children when visible', () => {
    const { getByText } = render(
      <BottomSheet visible={true} onClose={() => {}}>
        <RNText>Sheet content</RNText>
      </BottomSheet>,
      { wrapper },
    );
    expect(getByText('Sheet content')).toBeTruthy();
  });

  it('does not crash when onClose provided', () => {
    const onClose = jest.fn();
    render(
      <BottomSheet visible={true} onClose={onClose}>
        <RNText>Content</RNText>
      </BottomSheet>,
      { wrapper },
    );
    expect(onClose).not.toHaveBeenCalled();
  });
});