export interface AuthResponse {
  access_token: string;
  token_type: string;
  username?: string;
  role?: string;
}

export interface MeInfo {
  id: number;
  username: string;
  role: string;
}

export interface UserRoute {
  id: number;
  model: string;
  provider_name: string;
  provider_base_url: string;
  provider_model: string;
  provider_api_type: string;
  provider_api_key_masked?: string;
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

export interface ProviderBalanceItem {
  currency: string;
  available_balance: number | null;
  components?: Record<string, number | null>;
}

export interface ProviderBalance {
  name: string;
  base_url: string;
  vendor: string | null;
  supported: boolean;
  status: "ok" | "error" | "unsupported";
  is_available: boolean | null;
  balances: ProviderBalanceItem[];
  error: string | null;
  queried_at?: string | null;
}

// 用户私有路由余额（/v1/user/routes/balance），附带路由标识
export interface RouteBalance extends ProviderBalance {
  route_id: number;
  model: string;
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
  cached_input_tokens?: number;
  cache_write_tokens?: number;
  cache_hit_rate?: number;
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
  cache_hit_rate?: number;
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

export type TabId =
  | "routes"
  | "providers"
  | "logs"
  | "charts"
  | "notifications"
  | "pricing";

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

// ========== 模型价格（/v1/admin/billing/pricing/models） ==========
export interface PriceSet {
  input_price: number | null;
  output_price: number | null;
  cache_read_price: number | null;
  cache_write_price: number | null;
}

// models.dev 原始价格键名（USD / CNY）
export interface RawPriceSet {
  input: number | null;
  output: number | null;
  cache_read: number | null;
  cache_write: number | null;
}

export type BillingPriceSource =
  | "yaml"
  | "yaml+models.dev"
  | "yaml-missing-price";

export interface BillingRulePricing {
  provider: string;
  provider_aliases: string[];
  provider_model_patterns: string[];
  match_mode: string;
  currency: string | null;
  unit: number;
  yaml_prices: PriceSet;
  token_tier_count: number;
  time_window_count: number;
  source_url: string | null;
  note: string | null;
  effective: BillingPriceSource;
  remote_full_id: string | null;
  remote_prices: PriceSet | null;
}

export interface RemotePricingEntry {
  provider_id: string;
  model: string;
  full_id: string;
  usd: RawPriceSet;
  cny: RawPriceSet;
  source_url: string;
  covered_by: { provider: string; match_mode: string } | null;
}

export interface PricingCatalogStatus {
  enabled: boolean;
  loaded?: boolean;
  source?: string | null;
  provider_count?: number;
  entry_count?: number;
  last_refresh_at?: string | null;
  last_error?: string | null;
  last_cache_write_error?: string | null;
  cache_path?: string;
  cache_age_seconds?: number | null;
  usd_to_cny?: number;
}

export interface ModelPricingResponse {
  rules: BillingRulePricing[];
  remote: {
    total: number;
    items: RemotePricingEntry[];
    providers: string[];
    usd_to_cny: number;
  };
  status: PricingCatalogStatus;
}
