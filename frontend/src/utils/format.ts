export function currencySymbol(currency?: string): string {
  if (currency === "CNY") return "¥";
  if (currency === "USD") return "$";
  return currency ? `${currency} ` : "";
}

export function formatCost(value?: number, currency?: string): string {
  const num = Number(value);
  if (!Number.isFinite(num)) return "-";
  return `${currencySymbol(currency)}${num.toFixed(num >= 100 ? 2 : 4)}`;
}

export function formatCurrencyTotals(
  currencyTotals: Record<string, number>,
): string {
  const entries = Object.entries(currencyTotals).filter(([, value]) =>
    Number.isFinite(value),
  );
  if (!entries.length) return "-";
  return entries
    .map(([currency, value]) => formatCost(value, currency))
    .join(" / ");
}

export function formatTokens(total: number): string {
  if (total >= 1_000_000) return `${(total / 1_000_000).toFixed(1)}M`;
  if (total >= 1000) return `${(total / 1000).toFixed(1)}k`;
  return String(total);
}

export function formatHitRate(value?: number): string {
  const num = Number(value);
  if (!Number.isFinite(num) || num === 0) return "-";
  return `${(num * 100).toFixed(1)}%`;
}

export function formatDateTime(value: string): string {
  return new Date(value).toLocaleString("zh-CN");
}

export function parseJsonDisplay(value: unknown): string {
  if (value == null || value === "") return "-";
  try {
    const parsed = typeof value === "string" ? JSON.parse(value) : value;
    return JSON.stringify(parsed, null, 2);
  } catch {
    return String(value);
  }
}

export function getMonthOptions(count = 12): string[] {
  const now = new Date();
  const months: string[] = [];
  for (let i = 0; i < count; i += 1) {
    const date = new Date(now.getFullYear(), now.getMonth() - i, 1);
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, "0");
    months.push(`${year}-${month}`);
  }
  return months;
}

export function cn(...classes: Array<string | false | null | undefined>): string {
  return classes.filter(Boolean).join(" ");
}
