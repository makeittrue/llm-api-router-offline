export interface AuthResponse {
  access_token: string;
  token_type: string;
}

export interface UserRoute {
  id: number;
  model: string;
  provider_name: string;
  provider_base_url: string;
  provider_model: string;
  provider_api_type: string;
  created_at: string;
}

export interface DefaultRouteConfig {
  enabled: boolean;
  models: string[];
  updated_at?: string;
}

export interface ProviderOption {
  name: string;
  base_url: string;
  api_type: string;
}

export interface GlobalProvider {
  name: string;
  base_url: string;
  api_type: string;
}

export interface CallLog {
  id: number;
  created_at: string;
  model: string;
  provider?: string;
  total_tokens?: number;
  estimated_cost?: number;
  billing_currency?: string;
  duration_ms?: number;
  status: string;
  request_messages?: string | unknown;
  log_meta?: string | unknown;
  billing_meta?: string | unknown;
  error_message?: string;
}

export interface LogsResponse {
  data: CallLog[];
  total: number;
}

export interface LogSummaryItem {
  model: string;
  call_count: number;
  total_prompt_tokens?: number;
  total_completion_tokens?: number;
  cached_input_tokens?: number;
  total_tokens?: number;
  estimated_cost?: number;
  billing_currency?: string;
  avg_duration_ms?: number;
}

export interface FeishuNotificationSettings {
  enabled: boolean;
  daily_summary_enabled: boolean;
  alerts_enabled: boolean;
  daily_summary_time: string;
  feishu_app_id: string;
  feishu_app_secret?: string;
  feishu_app_secret_configured?: boolean;
  feishu_receive_id_type: string;
  feishu_receive_id: string;
  updated_at?: string;
  message?: string;
}

export type TabId = "routes" | "providers" | "logs" | "charts" | "notifications";

export interface RouteFormData {
  model: string;
  provider_name: string;
  provider_base_url: string;
  provider_api_key: string;
  provider_model: string;
  provider_api_type: string;
}

export interface DashboardStats {
  routeCount: number;
  providerCount: number;
  totalCalls: number;
  totalTokens: number | string;
  totalCost: string;
}
