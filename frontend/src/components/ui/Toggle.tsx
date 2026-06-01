import { cn } from "@/utils/format";

interface ToggleProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: string;
  description?: string;
  layout?: "inline" | "field";
}

function SwitchControl({
  checked,
  onChange,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className={cn(
        "relative h-6 w-11 shrink-0 rounded-full transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2",
        checked ? "bg-brand-600" : "bg-slate-300",
      )}
    >
      <span
        className={cn(
          "absolute top-0.5 left-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform",
          checked ? "translate-x-5" : "translate-x-0",
        )}
      />
    </button>
  );
}

export function Toggle({
  checked,
  onChange,
  label,
  description,
  layout = "inline",
}: ToggleProps) {
  if (layout === "field") {
    return (
      <div className="flex h-full flex-col">
        <span className="mb-1.5 block text-sm font-medium text-slate-700">
          {label}
        </span>
        <div className="flex min-h-[42px] items-center justify-between rounded-lg border border-slate-300 bg-white px-3 py-2 shadow-sm">
          <span className="text-sm text-slate-600">
            {checked ? "已启用" : "已关闭"}
          </span>
          <SwitchControl checked={checked} onChange={onChange} />
        </div>
        {description ? (
          <p className="mt-1 min-h-[1.25rem] text-xs text-slate-500">{description}</p>
        ) : (
          <p className="mt-1 min-h-[1.25rem]" aria-hidden="true" />
        )}
      </div>
    );
  }

  return (
    <div className="flex items-start gap-3 rounded-lg border border-slate-200 bg-slate-50/80 p-4">
      <SwitchControl checked={checked} onChange={onChange} />
      <div>
        <span className="block text-sm font-medium text-slate-800">{label}</span>
        {description ? (
          <span className="mt-1 block text-xs text-slate-500">{description}</span>
        ) : null}
      </div>
    </div>
  );
}
