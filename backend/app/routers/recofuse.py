"""推荐策略熔断接口：读取监控数据集计算熔断结果，维护熔断明细数据集。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.recofuse import RecofuseService

router = APIRouter(prefix="/api/recofuse", tags=["推荐策略熔断"])

service = RecofuseService()

LIST_FIELDS = ["熔断编号", "策略编号", "推荐场景", "监控数据集", "检测编号", "基线版本", "处置结论", "推荐可用性", "处理批次"]
STATUSES = ["草稿", "熔断复核中", "已生效", "已归档"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按熔断编号检索"),
    status: str | None = Query(default=None, description="草稿、熔断复核中、已生效、已归档"),
    batch: str | None = Query(default=None, description="按处理批次核对熔断明细"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按熔断编号、状态与处理批次过滤熔断明细列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, batch=batch, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出推荐策略熔断清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "recofuse", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条推荐策略熔断明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"推荐策略熔断明细 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条熔断明细，业务记录按完整业务凭证复核，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="推荐策略熔断明细已登记", entry=entry)


@router.post("/compute", response_model=ActionResult)
def compute_entry(payload: EntryPayload) -> ActionResult:
    """读取监控数据集并计算策略熔断结果：生成熔断明细、联动场景可用性并投递待处理。"""
    strategy_code = str(payload.values.get("策略编号") or "").strip()
    if not strategy_code:
        return ActionResult(ok=False, message="缺少必填字段：策略编号")
    batch = str(payload.values.get("处理批次") or "").strip() or None
    entry, message = service.compute_circuit_break(strategy_code, batch)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条熔断明细执行提交熔断复核、确认生效、归档明细；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or payload.action or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
