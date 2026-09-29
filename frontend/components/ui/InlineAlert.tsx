interface InlineAlertProps {
  variant: "error" | "warning" | "success";
  children: React.ReactNode;
  action?: { label: string; onClick: () => void };
}

const VARIANT_CLASS: Record<InlineAlertProps["variant"], string> = {
  error: "border-accent-red/30 bg-accent-red/5 text-accent-red-text",
  warning: "border-accent-yellow/30 bg-accent-yellow/5 text-accent-yellow-text",
  success: "border-accent-green/30 bg-accent-green/5 text-accent-green-text",
};

/**
 * Live-region politeness per variant. This is the whole reason "success" is
 * its own state rather than being folded into "warning":
 *
 *   role="alert"   -> aria-live="assertive". The screen reader interrupts
 *                     whatever it is currently saying. Correct for a load
 *                     failure, wrong for "vote counted": being interrupted
 *                     mid-sentence to hear good news is the same class of
 *                     bug as announcing good news in a warning colour.
 *   role="status"   -> aria-live="polite". Queued until the current
 *                     utterance finishes. Right for success.
 *
 * Success used to render as variant="warning" (components/user/SuggestionBox.tsx),
 * so every successful vote and submission was announced assertively *and*
 * painted in the warning palette -- indistinguishable from a real problem
 * by both sight and sound. The three states are now distinct; do not
 * collapse them back.
 */
const VARIANT_ROLE: Record<InlineAlertProps["variant"], "alert" | "status"> = {
  error: "alert",
  warning: "alert",
  success: "status",
};

/**
 * A single-color bordered banner for error/warning/success text, with an
 * optional inline action (e.g. "Retry").
 */
export default function InlineAlert({ variant, children, action }: InlineAlertProps) {
  return (
    <div
      role={VARIANT_ROLE[variant]}
      className={`flex flex-col gap-2 rounded-md border px-3 py-2 text-sm ${VARIANT_CLASS[variant]}`}
    >
      <p>{children}</p>
      {action && (
        <button type="button" onClick={action.onClick} className="self-start text-xs font-medium underline">
          {action.label}
        </button>
      )}
    </div>
  );
}
