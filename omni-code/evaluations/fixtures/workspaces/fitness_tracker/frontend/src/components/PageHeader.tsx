interface PageHeaderProps {
  title: string;
  description?: string;
  action?: React.ReactNode;
}

export default function PageHeader({ title, description, action }: PageHeaderProps) {
  return (
    <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between mb-8">
      <div>
        <h1 className="text-2xl font-bold text-text-primary tracking-tight">{title}</h1>
        {description && (
          <p className="text-sm text-text-tertiary mt-1">{description}</p>
        )}
      </div>
      {action}
    </div>
  );
}
