import React from 'react';
import { EmptyState } from '../components/EmptyState';

type Props = {
  title: string;
  message?: string;
};

export function PlaceholderScreen({ title, message }: Props) {
  return <EmptyState title={title} message={message ?? 'Coming soon'} />;
}