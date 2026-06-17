import { fetchJson } from "./client";
import type {
  CallLog,
  DefaultRouteConfig,
  FeishuNotificationSettings,
  GlobalProvider,
  LogSummaryItem,
  LogsResponse,
  ProviderOption,
  UserRoute,
} from "@/types/api";

export async function getUserRoutes() {
  return fetchJson<{ routes: UserRoute[] }>("/v1/user/routes");
}

export async function createUserRoute(body: Record<string, string>) {
  return fetchJson<UserRoute>("/v1/user/routes", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function updateUserRoute(
  id: number,
  body: Record<string, string>,
) {
  return fetchJson<UserRoute>(`/v1/user/routes/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function deleteUserRoute(id: number) {
  const response = await fetchJson<{ message?: string }>(
    `/v1/user/routes/${id}`,
    { method: "DELETE" },
  );
  return response;
}

export async function getDefaultRoute() {
  return fetchJson<DefaultRouteConfig>("/v1/user/default-route");
}

export async function updateDefaultRoute(body: DefaultRouteConfig) {
  return fetchJson<DefaultRouteConfig>("/v1/user/default-route", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function getBillingProviders() {
  return fetchJson<{ providers: ProviderOption[] }>(
    "/v1/admin/billing/providers",
  );
}

export async function getGlobalProviders() {
  return fetchJson<{ providers: GlobalProvider[] }>("/v1/admin/providers");
}

export async function getLogs(params: {
  limit: number;
  offset: number;
  model?: string;
}) {
  const search = new URLSearchParams({
    limit: String(params.limit),
    offset: String(params.offset),
  });
  if (params.model) search.set("model", params.model);
  return fetchJson<LogsResponse>(`/v1/logs?${search.toString()}`);
}

export async function getLogDetail(logId: number) {
  return fetchJson<{ data: CallLog }>(`/v1/logs/${logId}`);
}

export async function getLogSummary(month?: string) {
  const url = month
    ? `/v1/logs/summary?month=${encodeURIComponent(month)}`
    : "/v1/logs/summary";
  return fetchJson<{ data: LogSummaryItem[] }>(url);
}

export async function getFeishuSettings() {
  return fetchJson<FeishuNotificationSettings>("/v1/user/notifications/feishu");
}

export async function updateFeishuSettings(
  body: Partial<FeishuNotificationSettings>,
) {
  return fetchJson<FeishuNotificationSettings>(
    "/v1/user/notifications/feishu",
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
}

export async function regenerateToken() {
  return fetchJson<{ access_token: string }>("/v1/user/token/regenerate", {
    method: "POST",
  });
}
