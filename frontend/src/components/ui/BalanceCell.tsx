import type { ProviderBalance } from "@/types/api";

// 各运营商余额分项字段的中文标签（键名来自后端归一化结果）
const COMPONENT_LABELS: Record<string, string> = {
  granted_balance: "赠金",
  topped_up_balance: "充值",
  voucher_balance: "代金券",
  cash_balance: "现金",
  recharge_amount: "充值",
  give_amount: "赠送",
  total_spend_amount: "累计消费",
};

function formatAmount(value: number | null | undefined): string {
  if (value === null || value === undefined) return "-";
  return value.toFixed(2);
}

export function BalanceCell({ balance }: { balance?: ProviderBalance }) {
  if (!balance) {
    return <span className="text-slate-400">—</span>;
  }
  if (balance.status === "unsupported") {
    return (
      <div>
        <span className="text-slate-400">不支持查询</span>
        {balance.error ? (
          <p className="mt-0.5 text-xs text-slate-400">{balance.error}</p>
        ) : null}
      </div>
    );
  }
  if (balance.status === "error") {
    return (
      <div>
        <span className="font-medium text-rose-600">查询失败</span>
        {balance.error ? (
          <p className="mt-0.5 max-w-xs break-all text-xs text-rose-500">
            {balance.error}
          </p>
        ) : null}
      </div>
    );
  }
  return (
    <div className="space-y-1">
      {balance.balances.length === 0 ? (
        <span className="text-slate-400">无余额数据</span>
      ) : null}
      {balance.balances.map((item, index) => (
        <div key={`${item.currency}-${index}`}>
          <span className="font-medium text-slate-900">
            {item.currency} {formatAmount(item.available_balance)}
          </span>
          {item.components && Object.keys(item.components).length > 0 ? (
            <p className="text-xs text-slate-500">
              {Object.entries(item.components)
                .map(
                  ([key, value]) =>
                    `${COMPONENT_LABELS[key] ?? key} ${formatAmount(value)}`,
                )
                .join(" / ")}
            </p>
          ) : null}
        </div>
      ))}
      {balance.is_available === false ? (
        <p className="text-xs font-medium text-rose-600">
          余额不足，可能无法调用
        </p>
      ) : null}
    </div>
  );
}
