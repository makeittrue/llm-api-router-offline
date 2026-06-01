import { useEffect, useState, type ReactNode } from "react";
import { Save } from "lucide-react";
import { getFeishuSettings, updateFeishuSettings } from "@/api/services";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Toggle } from "@/components/ui/Toggle";
import { LoadingState } from "@/components/ui/DataTable";
import { useToast } from "@/context/ToastContext";
import type { FeishuNotificationSettings } from "@/types/api";
import { formatDateTime } from "@/utils/format";

function FormSection({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: ReactNode;
}) {
  return (
    <section className="space-y-4">
      <div>
        <h3 className="text-sm font-semibold text-slate-900">{title}</h3>
        {description ? (
          <p className="mt-1 text-sm text-slate-500">{description}</p>
        ) : null}
      </div>
      {children}
    </section>
  );
}

export function NotificationsPage() {
  const { showToast } = useToast();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [settings, setSettings] = useState<FeishuNotificationSettings>({
    enabled: false,
    daily_summary_enabled: false,
    alerts_enabled: false,
    daily_summary_time: "09:00",
    feishu_app_id: "",
    feishu_receive_id_type: "open_id",
    feishu_receive_id: "",
  });
  const [secret, setSecret] = useState("");
  const [statusText, setStatusText] = useState("未配置通知");

  const load = async () => {
    setLoading(true);
    try {
      const data = await getFeishuSettings();
      setSettings(data);
      setSecret("");
      renderStatus(data);
    } catch (error) {
      showToast(error instanceof Error ? error.message : "加载通知设置失败", "error");
      setStatusText("加载失败，请稍后重试");
    } finally {
      setLoading(false);
    }
  };

  const renderStatus = (data: FeishuNotificationSettings) => {
    const summaryState = data.daily_summary_enabled
      ? `日报 ${data.daily_summary_time || "09:00"}`
      : "日报关闭";
    const alertState = data.alerts_enabled ? "预警开启" : "预警关闭";
    setStatusText(
      data.enabled
        ? `通知已启用 · ${summaryState} · ${alertState}${data.updated_at ? ` · 更新于 ${formatDateTime(data.updated_at)}` : ""}`
        : `通知未启用${data.updated_at ? ` · 最近保存于 ${formatDateTime(data.updated_at)}` : ""}`,
    );
  };

  useEffect(() => {
    load();
  }, []);

  const handleSave = async () => {
    setSaving(true);
    try {
      const data = await updateFeishuSettings({
        enabled: settings.enabled,
        daily_summary_enabled: settings.daily_summary_enabled,
        alerts_enabled: settings.alerts_enabled,
        feishu_app_id: settings.feishu_app_id.trim(),
        feishu_app_secret: secret.trim(),
        feishu_receive_id_type: settings.feishu_receive_id_type,
        feishu_receive_id: settings.feishu_receive_id.trim(),
        daily_summary_time: settings.daily_summary_time || "09:00",
      });
      setSettings(data);
      setSecret("");
      renderStatus(data);
      showToast(data.message || "通知设置已保存", "success");
    } catch (error) {
      showToast(error instanceof Error ? error.message : "保存失败", "error");
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <LoadingState />;

  return (
    <div className="mx-auto max-w-4xl space-y-8">
      <div>
        <h2 className="text-lg font-semibold text-slate-900">飞书通知设置</h2>
        <p className="mt-1 text-sm text-slate-500">
          按用户独立配置日报与阈值预警。App Secret 保存后不会明文回显。
        </p>
      </div>

      <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
        飞书通知由当前登录用户自行配置。若不修改 App Secret，保存时可留空。
      </div>

      <FormSection title="通知开关" description="控制飞书消息的总开关与日报、预警行为。">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Toggle
            layout="field"
            checked={settings.enabled}
            onChange={(enabled) => setSettings((prev) => ({ ...prev, enabled }))}
            label="启用飞书通知"
          />
          <Toggle
            layout="field"
            checked={settings.daily_summary_enabled}
            onChange={(daily_summary_enabled) =>
              setSettings((prev) => ({ ...prev, daily_summary_enabled }))
            }
            label="启用每日日报"
          />
          <Toggle
            layout="field"
            checked={settings.alerts_enabled}
            onChange={(alerts_enabled) =>
              setSettings((prev) => ({ ...prev, alerts_enabled }))
            }
            label="启用阈值预警"
          />
          <Input
            label="每日日报时间"
            type="time"
            value={settings.daily_summary_time || "09:00"}
            onChange={(event) =>
              setSettings((prev) => ({
                ...prev,
                daily_summary_time: event.target.value,
              }))
            }
            hint="按服务器时区发送上一自然日用量摘要"
          />
        </div>
      </FormSection>

      <FormSection title="飞书应用凭据" description="在飞书开放平台创建应用后填写。">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Input
            label="飞书 App ID *"
            value={settings.feishu_app_id}
            onChange={(event) =>
              setSettings((prev) => ({ ...prev, feishu_app_id: event.target.value }))
            }
            placeholder="请输入飞书应用 App ID"
          />
          <Input
            label="飞书 App Secret *"
            type="password"
            value={secret}
            onChange={(event) => setSecret(event.target.value)}
            placeholder="首次保存必填；留空则保持不变"
            hint={
              settings.feishu_app_secret_configured
                ? "App Secret 已保存；留空表示保持不变"
                : "尚未配置 App Secret"
            }
          />
        </div>
      </FormSection>

      <FormSection title="消息接收人" description="指定日报与预警的发送目标。">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Select
            label="接收人类型 *"
            value={settings.feishu_receive_id_type}
            onChange={(event) =>
              setSettings((prev) => ({
                ...prev,
                feishu_receive_id_type: event.target.value,
              }))
            }
          >
            <option value="open_id">open_id</option>
            <option value="user_id">user_id</option>
            <option value="union_id">union_id</option>
            <option value="email">email</option>
            <option value="chat_id">chat_id</option>
          </Select>
          <Input
            label="接收人 ID *"
            value={settings.feishu_receive_id}
            onChange={(event) =>
              setSettings((prev) => ({ ...prev, feishu_receive_id: event.target.value }))
            }
            placeholder="例如 open_id / email / chat_id"
          />
        </div>
      </FormSection>

      <div className="flex flex-col gap-4 border-t border-slate-200 pt-6 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-sm leading-relaxed text-slate-500">{statusText}</p>
        <Button onClick={handleSave} disabled={saving} className="shrink-0">
          <Save className="h-4 w-4" />
          {saving ? "保存中..." : "保存通知设置"}
        </Button>
      </div>
    </div>
  );
}
