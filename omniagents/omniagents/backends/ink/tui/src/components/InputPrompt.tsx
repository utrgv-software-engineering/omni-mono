/**
 * Component for user input (now uses optimized implementation)
 */

import React from 'react';
import { MultiLineInputOptimized } from './MultiLineInputOptimized.js';

export interface InputPromptProps {
  onSubmit: (message: string) => void;
  disabled?: boolean;
  placeholder?: string;
  history?: string[]; // Message history for up/down navigation
}

export const InputPrompt = React.memo<InputPromptProps>(({
  onSubmit,
  disabled = false,
  placeholder = 'Type your message...',
  history = [],
}) => {
  return (
    <MultiLineInputOptimized
      onSubmit={onSubmit}
      disabled={disabled}
      placeholder={placeholder}
      history={history}
      maxHeight={20}
    />
  );
}, (prevProps, nextProps) => {
  return prevProps.disabled === nextProps.disabled &&
         (prevProps.history?.length ?? 0) === (nextProps.history?.length ?? 0) &&
         prevProps.placeholder === nextProps.placeholder;
});
