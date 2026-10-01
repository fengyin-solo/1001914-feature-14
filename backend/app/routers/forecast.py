"""功率预测接口：维护功率预测单，覆盖生成预测、登记偏差超标、复核预测等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.forecast import ForecastService

router = APIRouter(prefix="/api/forecast", tags=["功率预测"])

service = ForecastService()

LIST_FIELDS = ["预测单号", "所属场站", "预测日期", "预测出力", "实际出力", "预测偏差", "考核电量", "预测状态"]
STATUSES = ["待生成", "已生成", "偏差超标", "已复核"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按预测单号检索"),
    station: str | None = Query(default=None, description="按所属场站检索"),
    status: str | None = Query(default=None, description="待生成、已生成、偏差超标、已复核"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按预测单号、所属场站与状态过滤功率预测列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(
        keyword=keyword, station=station, status=status, page=page, size=size
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/stats")
def forecast_stats(
    station: str | None = Query(default=None, description="按所属场站统计"),
) -> dict[str, Any]:
    """功率预测看板数字：与列表共用同一份数据、同一套状态口径。"""
    return service.stats(station=station)


@router.get("/export")
def export_entries(
    station: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """导出功率预测清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(station=station, status=status, page=1, size=10000)
    return {"module": "forecast", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条功率预测单明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"功率预测单 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条功率预测单，缺字段或数字不合法时说明原因而不是静默丢弃。

    同一预测单号重复提交会更新原单，不会新增一行。
    """
    entry, message = service.create_entry(payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message="功率预测单已登记", entry=entry)


@router.put("/{entry_id}", response_model=ActionResult)
def update_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    """把字段修改保存到同一份预测单；已复核单据锁定，非法字段会给出原因。"""
    entry, message = service.update_entry(entry_id, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message="功率预测单已保存", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条功率预测单执行生成预测、登记偏差超标、复核预测。

    动作携带的字段（实际出力、预测偏差等）与状态变更在同一份数据上一次提交；
    状态不允许或字段不合法时整体不生效，并返回可读原因。
    """
    values = dict(payload.values)
    action = str(values.pop("action", "") or "").strip()
    entry, message = service.run_action(entry_id, action, values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
