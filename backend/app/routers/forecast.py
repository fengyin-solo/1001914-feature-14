"""功率预测接口：维护功率预测单，覆盖登记、生成预测、登记偏差超标、复核预测等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.forecast import ForecastService

router = APIRouter(prefix="/api/forecast", tags=["功率预测"])

service = ForecastService()

LIST_FIELDS = ["预测单号", "所属场站", "预测日期", "预测出力", "实际出力", "预测偏差", "考核电量", "预测状态"]
STATUSES = ["待生成", "已生成", "偏差超标", "已复核"]


@router.get("/stats", response_model=dict[str, Any])
def stats() -> dict[str, Any]:
    """列表页统计卡片：待生成、待复核、偏差超标天数与预测准确率，随数据实时变化。"""
    return service.summary()


@router.get("/export")
def export_entries(
    keyword: str | None = None,
    status: str | None = None,
    site: str | None = None,
    date: str | None = None,
) -> dict[str, Any]:
    """导出功率预测清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(keyword=keyword, status=status, site=site, date=date, page=1, size=10000)
    return {"module": "forecast", "total": total, "items": items}


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按预测单号检索"),
    status: str | None = Query(default=None, description="待生成、已生成、偏差超标、已复核"),
    site: str | None = Query(default=None, description="按所属场站检索"),
    date: str | None = Query(default=None, description="按预测日期精确检索"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按预测单号、场站、日期与状态过滤功率预测列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, site=site, date=date, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一张预测单；缺字段、格式错误或单号重复时都给出可读原因，绝不静默丢弃。"""
    entry, message = service.create_entry(payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条功率预测单明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"功率预测单 {entry_id} 不存在或已归档")
    return entry


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条预测单执行生成预测、登记偏差超标、复核预测。

    预测出力、实际出力、预测偏差、考核电量随动作一起提交，写入与复核始终落在
    同一张单上；重复提交不新增行，不允许的动作会被拦下并说明原因。
    """
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
