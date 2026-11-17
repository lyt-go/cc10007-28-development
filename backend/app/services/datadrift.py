"""数据漂移业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "datadrift"
REQUIRED_FIELDS = ["检测编号", "监控数据集", "基线版本"]
STATUS_ORDER = ["草稿", "规则评审中", "已启用", "已关闭"]
ACTION_RULES = {"提交规则评审": "规则评审中", "启用检测": "已启用", "关闭检测": "已关闭"}
NEGATIVE_ACTIONS = []


class DatadriftService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        pending: bool | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("检测编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if pending is not None:
            rows = [row for row in rows if bool(row.get("pending")) == pending]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"漂移检测 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于数据漂移可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"漂移检测已{action}"

    def deliver_pending(self, entry_id: int, source: str) -> tuple[dict[str, Any] | None, str]:
        """数据漂移待处理明细的投递入口：推荐策略熔断明细生效后把关联检测转入待处理。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"漂移检测 {entry_id} 不存在或已归档"
        entry["pending"] = True
        entry["待处理来源"] = source
        return entry, f"漂移检测 {entry.get('检测编号')} 已转入数据漂移待处理明细"
