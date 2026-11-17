"""推荐策略熔断业务规则：基线漂移驱动的熔断准入、竞态归一与可用性联动。

新增流程：读取推荐场景召回记录（智能推荐）与漂移检测监控数据集（数据漂移），
计算策略熔断结果，生成供上线使用的推荐策略熔断明细数据集。
"""
from __future__ import annotations

import threading
from datetime import date
from typing import Any

from app.services.datadrift import DatadriftService
from app.services.recommend import RecommendService
from app.store import store

MODULE = "recofuse"
REQUIRED_FIELDS = ["熔断编号", "策略编号", "推荐场景", "监控数据集", "处理批次"]
STATUS_ORDER = ["草稿", "熔断复核中", "已生效", "已归档"]
ACTION_RULES = {"提交熔断复核": "熔断复核中", "确认生效": "已生效", "归档明细": "已归档"}
NEGATIVE_ACTIONS = ["归档明细"]

DRIFT_ENABLED_STATUS = "已启用"
CONCLUSION_FUSED = "熔断暂停"
CONCLUSION_PASS = "放行可用"
CONCLUSION_NOT_READY = "监控未就绪"

# 竞态归一锁：同一场景的熔断计算与生效确认必须串行，保证只留存一个生效明细数据集。
_LOCK = threading.Lock()


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class RecofuseService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        batch: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("熔断编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if batch:
            rows = [row for row in rows if batch in str(row.get("处理批次", ""))]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """登记熔断明细：业务记录按完整业务凭证复核，缺关键字段不允许登记。"""
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        for field in ("检测编号", "基线版本", "漂移阈值", "最近漂移值", "处置结论", "推荐可用性", "最后可用召回结果"):
            if values.get(field) is not None and str(values.get(field)).strip():
                entry[field] = values.get(field)
        entry.setdefault("处置结论", CONCLUSION_NOT_READY)
        entry.setdefault("推荐可用性", "可用")
        entry["业务凭证"] = str(values.get("业务凭证") or "").strip() or self._credential(entry)
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        with _LOCK:
            entry = store.find(MODULE, entry_id)
            if entry is None:
                return None, f"推荐策略熔断明细 {entry_id} 不存在或已归档"
            if action not in ACTION_RULES:
                return None, f"动作「{action}」不属于推荐策略熔断可执行范围"
            target = ACTION_RULES[action]
            if target not in STATUS_ORDER:
                return None, f"目标状态「{target}」不在允许的状态序列里"
            if target == "已生效":
                self._retire_active(str(entry.get("推荐场景") or ""), keep_id=entry_id)
            entry["status"] = target
            entry["pending"] = target in ("草稿", "熔断复核中")
            entry["abnormal"] = action in NEGATIVE_ACTIONS
            return entry, f"推荐策略熔断明细已{action}"

    def compute_circuit_break(
        self,
        strategy_code: str,
        batch: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        """读取监控数据集并计算策略熔断结果。

        流程：读推荐场景召回记录 → 找同一监控数据集的已启用漂移检测 → 核对漂移阈值 →
        一并确认推荐可用性与漂移处置结论 → 生成熔断明细（同场景竞态只留存一个生效数据集）→
        超阈值时投递数据漂移待处理明细并切换场景可用性，保留最后可用召回结果。
        """
        with _LOCK:
            strategy = self._find_strategy(strategy_code)
            if strategy is None:
                return None, f"推荐策略 {strategy_code} 不存在或已归档"
            scenario = str(strategy.get("推荐场景") or "").strip()
            dataset = str(strategy.get("监控数据集") or "").strip()
            if not scenario or not dataset:
                return None, f"推荐策略 {strategy_code} 缺少推荐场景或监控数据集绑定，业务凭证不完整"
            batch_no = (batch or "").strip() or self._next_batch()
            detection = self._enabled_detection(dataset)
            if detection is None:
                entry = self._append_entry(
                    strategy=strategy,
                    detection=None,
                    batch_no=batch_no,
                    conclusion=CONCLUSION_NOT_READY,
                    availability=str(strategy.get("可用性") or "可用"),
                    status="草稿",
                )
                return entry, (
                    f"监控数据集「{dataset}」未完成启用，草稿或未完成监控数据不能触发正式熔断，"
                    f"熔断明细 {entry['熔断编号']} 暂存草稿"
                )
            threshold = _to_float(detection.get("漂移阈值"))
            latest = _to_float(detection.get("最近漂移值"))
            if threshold is None or latest is None:
                return None, f"漂移检测 {detection.get('检测编号')} 的漂移阈值或最近漂移值缺失，监控数据不完整"
            over = latest > threshold
            conclusion = CONCLUSION_FUSED if over else CONCLUSION_PASS
            availability = "暂停" if over else "可用"
            # 竞态归一：同场景已生效明细先归档，只留存本次计算生成的熔断明细数据集。
            self._retire_active(scenario, keep_id=None)
            entry = self._append_entry(
                strategy=strategy,
                detection=detection,
                batch_no=batch_no,
                conclusion=conclusion,
                availability=availability,
                status="已生效",
            )
            # 可用性联动走智能推荐的受控入口：暂停时保留最后可用召回结果，放行时刷新召回快照。
            recall_snapshot = None if over else f"召回快照-{strategy_code}-{batch_no}"
            _, avail_message = RecommendService().apply_availability(
                int(strategy["id"]), availability, recall_snapshot
            )
            message = (
                f"推荐策略熔断明细 {entry['熔断编号']} 已生效：漂移值 {latest} "
                f"{'超过' if over else '未超过'}阈值 {threshold}，处置结论「{conclusion}」，{avail_message}"
            )
            if over:
                _, deliver_message = DatadriftService().deliver_pending(
                    int(detection["id"]), f"推荐策略熔断明细{entry['熔断编号']}"
                )
                message = f"{message}；{deliver_message}"
            return entry, message

    def _find_strategy(self, strategy_code: str) -> dict[str, Any] | None:
        for row in store.rows("recommend"):
            if str(row.get("策略编号", "")) == strategy_code:
                return row
        return None

    def _enabled_detection(self, dataset: str) -> dict[str, Any] | None:
        """同一监控数据集的检测记录里取已启用的一条；草稿或评审中的数据不参与正式熔断。"""
        candidates = [
            row for row in store.rows("datadrift")
            if str(row.get("监控数据集", "")) == dataset and row.get("status") == DRIFT_ENABLED_STATUS
        ]
        candidates.sort(key=lambda row: int(row.get("id", 0)))
        return candidates[0] if candidates else None

    def _retire_active(self, scenario: str, keep_id: int | None) -> None:
        """竞态发生时只留存一个生效的推荐策略熔断明细数据集：同场景其余生效明细归档。"""
        for row in store.rows(MODULE):
            if row.get("id") == keep_id:
                continue
            if str(row.get("推荐场景", "")) == scenario and row.get("status") == "已生效":
                row["status"] = "已归档"
                row["pending"] = False

    def _append_entry(
        self,
        *,
        strategy: dict[str, Any],
        detection: dict[str, Any] | None,
        batch_no: str,
        conclusion: str,
        availability: str,
        status: str,
    ) -> dict[str, Any]:
        rows = store.rows(MODULE)
        seq = max((int(row.get("id", 0)) for row in rows), default=0) + 1
        entry: dict[str, Any] = {
            "id": seq,
            "熔断编号": f"FUSE-{seq:04d}",
            "策略编号": strategy.get("策略编号"),
            "推荐场景": strategy.get("推荐场景"),
            "监控数据集": strategy.get("监控数据集"),
            "检测编号": detection.get("检测编号") if detection else "",
            "基线版本": detection.get("基线版本") if detection else "",
            "漂移阈值": detection.get("漂移阈值") if detection else "",
            "最近漂移值": detection.get("最近漂移值") if detection else "",
            "处置结论": conclusion,
            "推荐可用性": availability,
            "最后可用召回结果": strategy.get("最后可用召回结果"),
            "处理批次": batch_no,
            "status": status,
            "pending": status in ("草稿", "熔断复核中"),
            "abnormal": False,
        }
        entry["业务凭证"] = self._credential(entry)
        rows.append(entry)
        return entry

    def _credential(self, entry: dict[str, Any]) -> str:
        """完整业务凭证：策略、检测与处理批次三段拼齐，复核时缺一不可。"""
        parts = [
            str(entry.get("策略编号") or "").strip(),
            str(entry.get("检测编号") or "未关联检测").strip(),
            str(entry.get("处理批次") or "").strip(),
        ]
        return "-".join(parts)

    def _next_batch(self) -> str:
        prefix = f"BATCH-{date.today():%Y%m%d}"
        serial = sum(1 for row in store.rows(MODULE) if str(row.get("处理批次", "")).startswith(prefix)) + 1
        return f"{prefix}-{serial:02d}"
