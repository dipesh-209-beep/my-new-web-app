interface InlineAlertProps {
  variant: "error" | "warning";
  children: React.ReactNode;
  action?: { label: string; onClick: () => void };
}

const VARIANT_CLASS: Record<InlineAlertProps["variant"], string> = {
  error: "border-accent-red/30 bg-accent-red/5 text-accent-red",
  warning: "border-accent-yellow/30 bg-accent-yellow/5 text-accent-yellow",
};

/**
 * A single-color bordered banner for error/warning text, with an optional
 * inline action (e.g. "Retry"). `role="alert"` is set for both variants --
 * a load failure is worth announcing to assistive tech whether or not it's
 * the hard-stop "error" variant.
 */
export default function InlineAlert({ variant, children, action }: InlineAlertProps) {
  return (
    <div role="alert" className={`flex flex-col gap-2 rounded-md border px-3 py-2 text-sm ${VARIANT_CLASS[variant]}`}>
      <p>{children}</p>
      {action && (
        <button type="button" onClick={action.onClick} className="self-start text-xs font-medium underline">
          {action.label}
        </button>
      )}
    </div>
  );
}
