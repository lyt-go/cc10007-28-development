"""智能推荐业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "recommend"
REQUIRED_FIELDS = ["策略编号", "推荐场景", "召回算法"]
STATUS_ORDER = ["草稿", "策略评审中", "已上线", "已下线"]
ACTION_RULES = {"提交策略评审": "策略评审中", "上线策略": "已上线", "下线策略": "已下线"}
NEGATIVE_ACTIONS = ["下线策略"]

# 推荐场景可用性取值：熔断流程只能在这两个值之间切换。
AVAILABILITY_VALUES = ["可用", "暂停"]
DRIFT_ENABLED_STATUS = "已启用"


def _drift_over_threshold(row: dict[str, Any]) -> bool:
    """读取漂移检测的阈值与最近漂移值，判断是否超阈值；数据缺失时不算告警。"""
    try:
        return float(row.get("最近漂移值")) > float(row.get("漂移阈值"))
    except (TypeError, ValueError):
        return False


class RecommendService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("策略编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
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
        for field in ("监控数据集", "最后可用召回结果"):
            if str(values.get(field) or "").strip():
                entry[field] = values.get(field)
        entry["可用性"] = "可用"
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"推荐策略 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于智能推荐可执行范围"
        if action == "上线策略":
            block = self._drift_alert_block(entry)
            if block is not None:
                return None, block
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"推荐策略已{action}"

    def apply_availability(
        self,
        entry_id: int,
        availability: str,
        recall_snapshot: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        """推荐策略熔断流程的受控写入口：只切换可用性并保留最后可用召回结果。

        智能推荐权限归属受控，熔断流程对本模块只读共享，可用性变更必须走这里，
        不允许越权直接改库，避免只读和共享边界被破坏。
        """
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"推荐策略 {entry_id} 不存在或已归档"
        if availability not in AVAILABILITY_VALUES:
            return None, f"可用性「{availability}」不在允许取值内"
        entry["可用性"] = availability
        if recall_snapshot is not None:
            entry["最后可用召回结果"] = recall_snapshot
        return entry, f"推荐场景「{entry.get('推荐场景')}」可用性已切换为{availability}"

    def _drift_alert_block(self, entry: dict[str, Any]) -> str | None:
        """上线竞态检查：同场景监控数据集存在已启用且超阈值的漂移告警时，熔断准入优先。

        上线请求与基线漂移告警相遇时只放行一个基线：先挂起上线请求，
        等熔断明细确认唯一采用基线后再上线，保证上线后的推荐只引用一个基线。
        """
        dataset = str(entry.get("监控数据集") or "").strip()
        if not dataset:
            return None
        for row in store.rows("datadrift"):
            if row.get("监控数据集") != dataset or row.get("status") != DRIFT_ENABLED_STATUS:
                continue
            if _drift_over_threshold(row):
                return (
                    f"监控数据集「{dataset}」存在基线漂移告警（{row.get('检测编号')}），"
                    "熔断准入优先，上线请求已挂起，请先完成推荐策略熔断计算"
                )
        return None
