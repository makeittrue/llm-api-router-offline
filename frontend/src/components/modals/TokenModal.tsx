import { useState } from "react";
import { Eye, EyeOff, Copy, RefreshCw } from "lucide-react";
import { regenerateToken } from "@/api/services";
import { useAuth } from "@/context/AuthContext";
import { useToast } from "@/context/ToastContext";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";

interface TokenModalProps {
  open: boolean;
  onClose: () => void;
}

export function TokenModal({ open, onClose }: TokenModalProps) {
  const { token, updateToken } = useAuth();
  const { showToast } = useToast();
  const [visible, setVisible] = useState(false);
  const [regenerating, setRegenerating] = useState(false);

  const copyToken = async () => {
    if (!token) {
      showToast("没有可复制的 Token", "error");
      return;
    }
    try {
      await navigator.clipboard.writeText(token);
      showToast("Token 已复制", "success");
    } catch {
      showToast("复制失败，请手动复制", "error");
    }
  };

  const handleRegenerate = async () => {
    if (!window.confirm("重新生成 Token 后，旧 Token 将立即失效。确定继续吗？")) {
      return;
    }
    setRegenerating(true);
    try {
      const data = await regenerateToken();
      updateToken(data.access_token);
      showToast("Token 已重新生成", "success");
    } catch (error) {
      showToast(error instanceof Error ? error.message : "重新生成失败", "error");
    } finally {
      setRegenerating(false);
    }
  };

  return (
    <Modal
      open={open}
      title="我的 API Token"
      description="调用私有路由 API 时使用 Bearer Token 鉴权。"
      onClose={onClose}
      size="md"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            关闭
          </Button>
          <Button variant="danger" onClick={handleRegenerate} disabled={regenerating}>
            <RefreshCw className="h-4 w-4" />
            {regenerating ? "生成中..." : "重新生成"}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="flex items-end gap-2">
          <div className="flex-1">
            <Input
              label="API Token"
              type={visible ? "text" : "password"}
              value={token || ""}
              readOnly
              className="font-mono"
            />
          </div>
          <Button variant="secondary" size="sm" onClick={() => setVisible((v) => !v)}>
            {visible ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
          </Button>
          <Button variant="secondary" size="sm" onClick={copyToken}>
            <Copy className="h-4 w-4" />
            复制
          </Button>
        </div>
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          请妥善保管 Token。若已泄露，请立即重新生成。
        </div>
        <p className="text-xs text-slate-500">
          请求头格式：<code className="rounded bg-slate-100 px-1">Authorization: Bearer &lt;token&gt;</code>
        </p>
      </div>
    </Modal>
  );
}
