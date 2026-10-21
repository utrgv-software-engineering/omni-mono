import Modal from './Modal';

interface ConfirmDialogProps {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: string;
  message: string;
  confirmLabel?: string;
}

export default function ConfirmDialog({
  open,
  onClose,
  onConfirm,
  title,
  message,
  confirmLabel = 'Delete',
}: ConfirmDialogProps) {
  return (
    <Modal open={open} onClose={onClose} title={title}>
      <p className="text-text-secondary mb-6">{message}</p>
      <div className="flex justify-end gap-3">
        <button
          onClick={onClose}
          className="px-4 py-2.5 text-sm font-medium text-text-secondary bg-surface-4 border border-border-default rounded-lg hover:bg-surface-3 hover:border-border-hover transition-colors cursor-pointer"
        >
          Cancel
        </button>
        <button
          onClick={() => {
            onConfirm();
            onClose();
          }}
          className="px-4 py-2.5 text-sm font-medium text-white bg-danger rounded-lg hover:bg-danger/80 active:scale-[0.98] transition-all cursor-pointer"
        >
          {confirmLabel}
        </button>
      </div>
    </Modal>
  );
}
