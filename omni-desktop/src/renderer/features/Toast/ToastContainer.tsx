import { useStore } from '@nanostores/react';
import { Toaster } from '@omniagents/ui/sonner';
import { isThemeDark } from '@omniagents/ui/themes';
import { CopyIcon } from 'lucide-react';
import { memo, useCallback, useEffect } from 'react';
import { toast } from 'sonner';

import type { ToastAction, ToastLevel } from '@/renderer/features/Toast/state';
import { $toasts, removeToast } from '@/renderer/features/Toast/state';
import { persistedStoreApi } from '@/renderer/services/store';

const copyToClipboard = async (text: string): Promise<void> => {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.style.position = 'fixed';
    textarea.style.opacity = '0';
    document.body.appendChild(textarea);
    textarea.select();
    try {
      document.execCommand('copy');
    } finally {
      document.body.removeChild(textarea);
    }
  }
};

const toastMethod: Record<ToastLevel, typeof toast.info> = {
  info: toast.info,
  success: toast.success,
  warning: toast.warning,
  error: toast.error,
};

export const ToastContainer = memo(() => {
  const dispatch = useCallback(
    (entry: {
      id: string;
      level: ToastLevel;
      title: string;
      description?: string;
      copyText?: string;
      action?: ToastAction;
      durationMs: number;
    }) => {
      const method = toastMethod[entry.level];
      method(entry.title, {
        id: entry.id,
        description: entry.description,
        duration: entry.durationMs > 0 ? entry.durationMs : Infinity,
        action: entry.action
          ? {
              label: entry.action.label,
              onClick: () => entry.action?.onClick(),
            }
          : entry.copyText
            ? {
                label: (
                  <span className="inline-flex items-center gap-1">
                    <CopyIcon className="size-3" />
                    Copy error
                  </span>
                ),
                onClick: () => void copyToClipboard(entry.copyText!),
              }
            : undefined,
        cancel: { label: 'Dismiss', onClick: () => toast.dismiss(entry.id) },
      });
      removeToast(entry.id);
    },
    []
  );

  useEffect(() => {
    for (const entry of $toasts.get()) {
      dispatch(entry);
    }
    return $toasts.subscribe((entries) => entries.forEach(dispatch));
  }, [dispatch]);

  const theme = useStore(persistedStoreApi.$atom).theme ?? 'omni';
  return <Toaster theme={isThemeDark(theme) ? 'dark' : 'light'} position="bottom-right" closeButton richColors />;
});
ToastContainer.displayName = 'ToastContainer';
