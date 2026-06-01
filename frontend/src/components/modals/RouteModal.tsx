import { useEffect, useMemo, useState } from "react";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
import { Select } from "@/components/ui/Select";
import type { ProviderOption, UserRoute } from "@/types/api";

const CUSTOM_PROVIDER = "__custom__";

interface RouteModalProps {
  open: boolean;
  route: UserRoute | null;
  providers: ProviderOption[];
  onClose: () => void;
  onSave: (payload: Record<string, string>, routeId?: number) => Promise<void>;
}

export function RouteModal({
  open,
  route,
  providers,
  onClose,
  onSave,
}: RouteModalProps) {
  const [model, setModel] = useState("");
  const [providerSelect, setProviderSelect] = useState("");
  const [customProviderName, setCustomProviderName] = useState("");
  const [providerBaseUrl, setProviderBaseUrl] = useState("");
  const [providerApiKey, setProviderApiKey] = useState("");
  const [providerModel, setProviderModel] = useState("");
  const [providerApiType, setProviderApiType] = useState("openai");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    if (route) {
      const isPreset = providers.some((item) => item.name === route.provider_name);
      setModel(route.model);
      setProviderSelect(isPreset ? route.provider_name : CUSTOM_PROVIDER);
      setCustomProviderName(isPreset ? "" : route.provider_name);
      setProviderBaseUrl(route.provider_base_url);
      setProviderApiKey("");
      setProviderModel(route.provider_model);
      setProviderApiType(route.provider_api_type || "openai");
    } else {
      setModel("");
      setProviderSelect("");
      setCustomProviderName("");
      setProviderBaseUrl("");
      setProviderApiKey("");
      setProviderModel("");
      setProviderApiType("openai");
    }
  }, [open, route, providers]);

  const showCustomProvider = providerSelect === CUSTOM_PROVIDER;

  const effectiveProviderName = useMemo(() => {
    if (providerSelect === CUSTOM_PROVIDER) return customProviderName.trim();
    return providerSelect.trim();
  }, [providerSelect, customProviderName]);

  const handleProviderChange = (value: string) => {
    setProviderSelect(value);
    if (value && value !== CUSTOM_PROVIDER) {
      const provider = providers.find((item) => item.name === value);
      if (provider) {
        setProviderBaseUrl(provider.base_url || "");
        if (provider.api_type) setProviderApiType(provider.api_type);
      }
    }
  };

  const handleSubmit = async () => {
    if (!model.trim() || !effectiveProviderName || !providerBaseUrl.trim() || !providerModel.trim()) {
      return;
    }
    if (!route && !providerApiKey.trim()) {
      return;
    }

    const payload: Record<string, string> = {
      model: model.trim(),
      provider_name: effectiveProviderName,
      provider_base_url: providerBaseUrl.trim(),
      provider_model: providerModel.trim(),
      provider_api_type: providerApiType,
    };
    if (providerApiKey.trim()) {
      payload.provider_api_key = providerApiKey.trim();
    }

    setSaving(true);
    try {
      await onSave(payload, route?.id);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      title={route ? "编辑路由" : "新增路由"}
      onClose={onClose}
      size="xl"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            取消
          </Button>
          <Button onClick={handleSubmit} disabled={saving}>
            {saving ? "保存中..." : "保存"}
          </Button>
        </>
      }
    >
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <Input
          label="模型名称 *"
          value={model}
          onChange={(event) => setModel(event.target.value)}
          placeholder="例如: my-gpt-4"
        />
        <div>
          <Select
            label="服务商名称 *"
            value={providerSelect}
            onChange={(event) => handleProviderChange(event.target.value)}
          >
            <option value="">请选择服务商</option>
            <option value={CUSTOM_PROVIDER}>自定义服务商</option>
            {providers.map((provider) => (
              <option key={provider.name} value={provider.name}>
                {provider.name}
              </option>
            ))}
          </Select>
          {showCustomProvider ? (
            <div className="mt-2">
              <Input
                value={customProviderName}
                onChange={(event) => setCustomProviderName(event.target.value)}
                placeholder="请输入自定义服务商名称"
              />
            </div>
          ) : null}
          <p className="mt-1 text-xs text-slate-500">
            预设服务商来自 config.yaml；自定义服务商默认不会命中计费规则。
          </p>
        </div>
        <Input
          label="API 地址 *"
          value={providerBaseUrl}
          onChange={(event) => setProviderBaseUrl(event.target.value)}
          placeholder="https://api.openai.com"
        />
        <Input
          label={`API Key ${route ? "" : "*"}`}
          type="password"
          value={providerApiKey}
          onChange={(event) => setProviderApiKey(event.target.value)}
          placeholder={route ? "留空表示不修改" : "你的 API Key"}
        />
        <Input
          label="服务商模型名 *"
          value={providerModel}
          onChange={(event) => setProviderModel(event.target.value)}
          placeholder="例如: gpt-4o"
        />
        <Select
          label="API 类型"
          value={providerApiType}
          onChange={(event) => setProviderApiType(event.target.value)}
        >
          <option value="openai">OpenAI 兼容</option>
        </Select>
      </div>
    </Modal>
  );
}
