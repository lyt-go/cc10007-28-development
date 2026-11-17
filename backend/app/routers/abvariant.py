"""实验变体接口：维护变体方案，覆盖提交变体审批、生效变体、下线变体等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.abvariant import AbvariantService

router = APIRouter(prefix="/api/abvariant", tags=["实验变体"])

service = AbvariantService()

LIST_FIELDS = ["变体编号", "关联实验", "变体名称", "变体描述", "分流占比", "配置参数集", "指标期望", "生效时间范围"]
STATUSES = ["草稿", "变体审批中", "已生效", "已下线"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按变体编号检索"),
    status: str | None = Query(default=None, description="草稿、变体审批中、已生效、已下线"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按变体编号与状态过滤实验变体列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条变体方案明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"变体方案 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条变体方案，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="变体方案已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条变体方案执行提交变体审批、生效变体、下线变体；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or payload.action or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出实验变体清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "abvariant", "total": total, "items": items}
