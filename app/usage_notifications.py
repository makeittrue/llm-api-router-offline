from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.config import AppConfig, FeishuConfig, UsageAlertRuleConfig, load_config
from app.logger import CallLogger
from app.notifiers import FeishuNotifier

_ALERT_METRIC_LABELS = {
    "daily_cost": "日费用",
    "daily_tokens": "日 Token",
    "daily_calls": "日调用次数",
}


@dataclass
class NotificationRunStats:
    sent: int = 0
    skipped: int = 0
    failed: int = 0

    def as_dict(self) -> dict[str, int]:
        return {"sent": self.sent, "skipped": self.skipped, "failed": self.failed}


class UsageNotificationService:
    def __init__(self, app_config: AppConfig, call_logger: CallLogger):
        self.app_config = app_config
        self.call_logger = call_logger
        self.notifications = app_config.notifications
        self.tz = ZoneInfo(self.notifications.timezone)

    @property
    def enabled(self) -> bool:
        return bool(self.notifications.enabled and self.notifications.feishu.enabled)

    @property
    def scheduler_interval_seconds(self) -> int:
        if self.notifications.alerts.enabled:
            return self.notifications.alerts.scan_interval_seconds
        return 60

    def now_local(self) -> datetime:
        return datetime.now(self.tz)

    def _parse_user_send_time(self, raw: str | None) -> time:
        candidate = (raw or self.notifications.daily_summary.send_time or "09:00").strip()
        try:
            hour_str, minute_str = candidate.split(":", 1)
            hour = int(hour_str)
            minute = int(minute_str)
        except ValueError as e:
            raise ValueError(f"用户日报时间格式必须为 HH:MM，当前为 {candidate!r}") from e
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError(f"用户日报时间超出范围，当前为 {candidate!r}")
        return time(hour=hour, minute=minute)

    def _window_for_local_date(self, target_date: date) -> tuple[str, str]:
        start_local = datetime.combine(target_date, time.min, tzinfo=self.tz)
        end_local = start_local + timedelta(days=1)
        return (
            start_local.astimezone(timezone.utc).isoformat(),
            end_local.astimezone(timezone.utc).isoformat(),
        )

    def _metric_value(self, row: dict[str, Any], metric: str) -> float:
        if metric == "daily_cost":
            return float(row.get("estimated_cost") or 0)
        if metric == "daily_tokens":
            return float(row.get("total_tokens") or 0)
        if metric == "daily_calls":
            return float(row.get("call_count") or 0)
        raise ValueError(f"不支持的告警指标: {metric}")

    def _build_user_notifier(self, settings: dict[str, Any]) -> FeishuNotifier:
        return FeishuNotifier(
            FeishuConfig(
                enabled=True,
                app_id=str(settings.get("feishu_app_id") or ""),
                app_secret=str(settings.get("feishu_app_secret") or ""),
                base_url=self.notifications.feishu.base_url,
                receive_id_type=str(
                    settings.get("feishu_receive_id_type")
                    or self.notifications.feishu.receive_id_type
                    or "open_id"
                ),
            )
        )

    def _has_complete_user_config(self, settings: dict[str, Any]) -> bool:
        return bool(
            settings.get("feishu_app_id")
            and settings.get("feishu_app_secret")
            and settings.get("feishu_receive_id")
        )

    def _top_models_text(self, models: list[dict[str, Any]]) -> str:
        if not models:
            return "无"
        parts: list[str] = []
        for item in models:
            model = item.get("model") or item.get("provider_model") or "unknown"
            call_count = int(item.get("call_count") or 0)
            total_tokens = int(item.get("total_tokens") or 0)
            estimated_cost = float(item.get("estimated_cost") or 0)
            parts.append(
                f"- {model}: {call_count} 次, {total_tokens} tokens, {estimated_cost:.4f} "
                f"{item.get('billing_currency') or self.app_config.billing.default_currency}"
            )
        return "\n".join(parts)

    def _build_daily_summary_message(
        self,
        username: str,
        target_date: date,
        rollup: dict[str, Any],
        models: list[dict[str, Any]],
    ) -> str:
        currency = rollup.get("billing_currency") or self.app_config.billing.default_currency
        no_usage = int(rollup.get("call_count") or 0) == 0
        summary_hint = "当日暂无调用记录\n" if no_usage else ""
        return (
            f"LLM Router 每日用量总结\n"
            f"用户: {username}\n"
            f"日期: {target_date.isoformat()}\n"
            f"{summary_hint}"
            f"调用次数: {int(rollup.get('call_count') or 0)}\n"
            f"总 Token: {int(rollup.get('total_tokens') or 0)}\n"
            f"输入 Token: {int(rollup.get('total_prompt_tokens') or 0)}\n"
            f"输出 Token: {int(rollup.get('total_completion_tokens') or 0)}\n"
            f"估算费用: {float(rollup.get('estimated_cost') or 0):.4f} {currency}\n"
            f"Top 模型:\n{self._top_models_text(models)}"
        )

    def _build_alert_message(
        self,
        username: str,
        target_date: date,
        rollup: dict[str, Any],
        rule: UsageAlertRuleConfig,
        observed_value: float,
        models: list[dict[str, Any]],
    ) -> str:
        currency = rollup.get("billing_currency") or self.app_config.billing.default_currency
        metric_label = _ALERT_METRIC_LABELS.get(rule.metric, rule.metric)
        observed_text = f"{observed_value:.4f} {currency}" if rule.metric == "daily_cost" else f"{int(observed_value)}"
        threshold_text = f"{rule.threshold:.4f} {currency}" if rule.metric == "daily_cost" else f"{int(rule.threshold)}"
        return (
            f"LLM Router 用量预警\n"
            f"用户: {username}\n"
            f"日期: {target_date.isoformat()}\n"
            f"规则: {rule.name}\n"
            f"指标: {metric_label}\n"
            f"当前值: {observed_text}\n"
            f"阈值: {threshold_text}\n"
            f"今日调用次数: {int(rollup.get('call_count') or 0)}\n"
            f"今日总 Token: {int(rollup.get('total_tokens') or 0)}\n"
            f"今日估算费用: {float(rollup.get('estimated_cost') or 0):.4f} {currency}\n"
            f"Top 模型:\n{self._top_models_text(models)}"
        )

    async def send_daily_summary_for_date(
        self,
        target_date: date,
        settings_list: list[dict[str, Any]] | None = None,
    ) -> dict[str, int]:
        stats = NotificationRunStats()
        if not self.enabled:
            return stats.as_dict()

        start_at, end_at = self._window_for_local_date(target_date)
        rollup_map = {
            int(item["user_id"]): item
            for item in self.call_logger.get_user_usage_rollups(start_at, end_at)
            if item.get("user_id") is not None
        }
        settings_rows = settings_list if settings_list is not None else self.call_logger.list_active_notification_settings()
        for settings in settings_rows:
            if not settings.get("daily_summary_enabled"):
                stats.skipped += 1
                continue
            if not self._has_complete_user_config(settings):
                print(f"[INFO] 跳过用户 {settings.get('username') or settings.get('user_id')} 的日报：飞书配置不完整")
                stats.skipped += 1
                continue

            user_id = int(settings.get("user_id") or 0)
            username = str(settings.get("username") or "").strip()
            if not user_id or not username:
                stats.skipped += 1
                continue

            rollup = rollup_map.get(user_id) or {
                "user_id": user_id,
                "username": username,
                "call_count": 0,
                "total_prompt_tokens": 0,
                "total_completion_tokens": 0,
                "total_tokens": 0,
                "cached_input_tokens": 0,
                "cache_write_tokens": 0,
                "estimated_cost": 0,
                "billing_currency": self.app_config.billing.default_currency,
                "avg_duration_ms": 0,
            }

            event_key = f"daily-summary:{target_date.isoformat()}:{user_id}"
            claimed = self.call_logger.claim_notification_event(
                event_key=event_key,
                event_type="daily_summary",
                user_id=user_id,
                username=username,
                event_date=target_date.isoformat(),
                payload={"kind": "daily_summary", "date": target_date.isoformat()},
            )
            if not claimed:
                stats.skipped += 1
                continue

            models = self.call_logger.get_model_usage_rollups(start_at, end_at, user_id=user_id)
            notifier = self._build_user_notifier(settings)
            try:
                response = await notifier.send_text(
                    str(settings.get("feishu_receive_id")),
                    self._build_daily_summary_message(username, target_date, rollup, models),
                )
                self.call_logger.update_notification_event(
                    event_key,
                    status="sent",
                    payload={
                        "kind": "daily_summary",
                        "date": target_date.isoformat(),
                        "receive_id": settings.get("feishu_receive_id"),
                        "response": response,
                    },
                )
                stats.sent += 1
            except Exception as e:
                self.call_logger.delete_notification_event(event_key)
                print(f"[WARN] 飞书日报发送失败 user={username}: {e}")
                stats.failed += 1
        return stats.as_dict()

    async def maybe_send_scheduled_daily_summary(self, now: datetime | None = None) -> dict[str, int]:
        stats = NotificationRunStats()
        if not self.enabled:
            return stats.as_dict()

        now_local = now.astimezone(self.tz) if now is not None else self.now_local()
        due_settings: list[dict[str, Any]] = []
        for settings in self.call_logger.list_active_notification_settings():
            if not settings.get("daily_summary_enabled"):
                continue
            try:
                scheduled_time = self._parse_user_send_time(str(settings.get("daily_summary_time") or ""))
            except ValueError as e:
                print(f"[WARN] 用户 {settings.get('username') or settings.get('user_id')} 的日报时间配置无效: {e}")
                stats.failed += 1
                continue
            if (now_local.hour, now_local.minute) >= (scheduled_time.hour, scheduled_time.minute):
                due_settings.append(settings)

        if not due_settings:
            return stats.as_dict()
        return await self.send_daily_summary_for_date(now_local.date() - timedelta(days=1), settings_list=due_settings)

    async def scan_alerts_for_date(self, target_date: date) -> dict[str, int]:
        stats = NotificationRunStats()
        if not (self.enabled and self.notifications.alerts.enabled):
            return stats.as_dict()

        start_at, end_at = self._window_for_local_date(target_date)
        rollup_map = {
            int(item["user_id"]): item
            for item in self.call_logger.get_user_usage_rollups(start_at, end_at)
            if item.get("user_id") is not None
        }
        for settings in self.call_logger.list_active_notification_settings():
            if not settings.get("alerts_enabled"):
                stats.skipped += 1
                continue
            if not self._has_complete_user_config(settings):
                stats.skipped += 1
                continue

            user_id = int(settings.get("user_id") or 0)
            username = str(settings.get("username") or "").strip()
            if not user_id or not username:
                stats.skipped += 1
                continue

            rollup = rollup_map.get(user_id)
            if rollup is None:
                stats.skipped += 1
                continue

            models = self.call_logger.get_model_usage_rollups(start_at, end_at, user_id=user_id)
            notifier = self._build_user_notifier(settings)
            for rule in self.notifications.alerts.rules:
                observed_value = self._metric_value(rollup, rule.metric)
                if observed_value < rule.threshold:
                    continue

                event_key = f"usage-alert:{rule.name}:{target_date.isoformat()}:{user_id}"
                claimed = self.call_logger.claim_notification_event(
                    event_key=event_key,
                    event_type="usage_alert",
                    user_id=user_id,
                    username=username,
                    event_date=target_date.isoformat(),
                    metric=rule.metric,
                    threshold_value=rule.threshold,
                    observed_value=observed_value,
                    payload={
                        "kind": "usage_alert",
                        "rule": rule.name,
                        "metric": rule.metric,
                        "date": target_date.isoformat(),
                    },
                )
                if not claimed:
                    stats.skipped += 1
                    continue

                try:
                    response = await notifier.send_text(
                        str(settings.get("feishu_receive_id")),
                        self._build_alert_message(
                            username=username,
                            target_date=target_date,
                            rollup=rollup,
                            rule=rule,
                            observed_value=observed_value,
                            models=models,
                        ),
                    )
                    self.call_logger.update_notification_event(
                        event_key,
                        status="sent",
                        payload={
                            "kind": "usage_alert",
                            "rule": rule.name,
                            "metric": rule.metric,
                            "date": target_date.isoformat(),
                            "receive_id": settings.get("feishu_receive_id"),
                            "response": response,
                        },
                    )
                    stats.sent += 1
                except Exception as e:
                    self.call_logger.delete_notification_event(event_key)
                    print(f"[WARN] 飞书预警发送失败 user={username} rule={rule.name}: {e}")
                    stats.failed += 1
        return stats.as_dict()

    async def run_scheduler(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            try:
                await self.maybe_send_scheduled_daily_summary()
                await self.scan_alerts_for_date(self.now_local().date())
            except Exception as e:
                print(f"[WARN] 用量通知调度执行失败: {e}")

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.scheduler_interval_seconds)
            except asyncio.TimeoutError:
                continue


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LLM Router 用量通知工具")
    parser.add_argument("--config", dest="config_path", default=None, help="配置文件路径，默认读取 LLM_ROUTER_CONFIG 或 config.yaml")
    subparsers = parser.add_subparsers(dest="command", required=True)

    daily_summary = subparsers.add_parser("daily-summary", help="发送指定日期的日报")
    daily_summary.add_argument("--date", dest="target_date", default=None, help="目标日期，格式 YYYY-MM-DD，默认上一自然日")

    scan_alerts = subparsers.add_parser("scan-alerts", help="扫描指定日期的阈值预警")
    scan_alerts.add_argument("--date", dest="target_date", default=None, help="目标日期，格式 YYYY-MM-DD，默认今天")

    subparsers.add_parser("run-scheduler", help="以前台方式运行内置通知调度器")
    return parser


def _parse_cli_date(raw: str | None, default_date: date) -> date:
    if not raw:
        return default_date
    return date.fromisoformat(raw)


async def _run_cli_async(args: argparse.Namespace) -> int:
    app_config = load_config(args.config_path)
    call_logger = CallLogger(app_config.log.db_path, app_config.billing)
    service = UsageNotificationService(app_config, call_logger)

    if not service.enabled:
        print("[INFO] notifications 或 feishu 通道未启用，跳过执行")
        return 0

    now_local = service.now_local()
    if args.command == "daily-summary":
        target_date = _parse_cli_date(args.target_date, now_local.date() - timedelta(days=1))
        stats = await service.send_daily_summary_for_date(target_date)
        print(f"[INFO] 日报发送完成: {stats}")
        return 0
    if args.command == "scan-alerts":
        target_date = _parse_cli_date(args.target_date, now_local.date())
        stats = await service.scan_alerts_for_date(target_date)
        print(f"[INFO] 预警扫描完成: {stats}")
        return 0
    if args.command == "run-scheduler":
        stop_event = asyncio.Event()
        try:
            await service.run_scheduler(stop_event)
        except KeyboardInterrupt:
            stop_event.set()
        return 0
    raise ValueError(f"未知命令: {args.command}")


def main() -> int:
    parser = _build_arg_parser()
    args = parser.parse_args()
    return asyncio.run(_run_cli_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
