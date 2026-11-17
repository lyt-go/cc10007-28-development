"""流失预警接口：维护预警模型，覆盖发起训练、部署模型、下线模型等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.churn import ChurnService

router = APIRouter(prefix="/api/churn", tags=["流失预警"])

service = ChurnService()

LIST_FIELDS = ["模型编号", "模型名称", "特征变量", "流失定义天", "训练数据集", "预测阈值", "模型准确率", "预警推送频率"]
STATUSES = ["草稿", "训练中", "已部署", "已下线"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按模型编号检索"),
    status: str | None = Query(default=None, description="草稿、训练中、已部署、已下线"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按模型编号与状态过滤流失预警列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条预警模型明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"预警模型 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条预警模型，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="预警模型已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条预警模型执行发起训练、部署模型、下线模型；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or payload.action or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出流失预警清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "churn", "total": total, "items": items}
