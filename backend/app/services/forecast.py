"""功率预测业务规则：预测单登记、数值校验、状态流转与指标统计都收在这里。

同一张预测单（按预测单号唯一）始终落在同一份数据上：登记写入、偏差更新、
复核流转都直接改这一条记录；重复提交只更新原单、不新增行，列表与概览读到
的始终是最新值，刷新页面或切换场站再回来都不会回退。
"""
from __future__ import annotations

import re
from typing import Any

from app.store import store

MODULE = "forecast"
REQUIRED_FIELDS = ["预测单号", "所属场站", "预测日期"]
TEXT_FIELDS = ["预测单号", "所属场站", "预测日期"]
NUMERIC_FIELDS = ["预测出力", "实际出力", "预测偏差", "考核电量"]
EDITABLE_FIELDS = TEXT_FIELDS + NUMERIC_FIELDS
STATUS_ORDER = ["待生成", "已生成", "偏差超标", "已复核"]
# 待生成等待出预测、偏差超标等待复核，这两种计入待处理；已复核后即退出待处理。
PENDING_STATUSES = {"待生成", "偏差超标"}
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NUMBER_PATTERN = re.compile(r"^-?\d+(?:\.\d+)?%?$")


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _number_text(value: Any) -> tuple[str | None, str | None]:
    """归一化数值字段：空值留空，数字可带小数/百分号，非法值返回可读原因。"""
    raw = _text(value)
    if not raw:
        return None, None
    if not NUMBER_PATTERN.match(raw):
        return None, "需为数字（可带小数与百分号）"
    return raw[:-1] if raw.endswith("%") else raw, None


class ForecastService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        site: str | None = None,
        date: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("预测单号", "") or "")]
        if site:
            rows = [row for row in rows if site in str(row.get("所属场站", "") or "")]
        if date:
            rows = [row for row in rows if str(row.get("预测日期", "") or "") == date]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [self._view(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        row = store.find(MODULE, entry_id)
        return self._view(row) if row is not None else None

    def summary(self) -> dict[str, Any]:
        """列表页统计卡片：数字全部按当前数据实时算，状态一变卡片就跟着变。"""
        rows = store.rows(MODULE)
        deviations: list[float] = []
        for row in rows:
            raw = _text(row.get("预测偏差"))
            try:
                deviations.append(abs(float(raw)))
            except ValueError:
                continue
        if deviations:
            accuracy = f"{max(0.0, 100.0 - sum(deviations) / len(deviations)):.1f}%"
        else:
            accuracy = "—"
        return {
            "待生成预测": sum(1 for row in rows if row.get("status") == "待生成"),
            "待复核预测": sum(1 for row in rows if row.get("status") == "偏差超标"),
            "偏差超标天数": sum(1 for row in rows if row.get("abnormal")),
            "预测准确率": accuracy,
        }

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """登记或补录预测单。预测单号唯一：重复提交更新原单，绝不新增第二行。"""
        number = _text(values.get("预测单号"))
        windfarm = _text(values.get("所属场站"))
        forecast_date = _text(values.get("预测日期"))
        missing = [
            name
            for name, value in (
                ("预测单号", number),
                ("所属场站", windfarm),
                ("预测日期", forecast_date),
            )
            if not value
        ]
        if missing:
            return None, f"缺少必填字段：{'、'.join(missing)}"
        if not DATE_PATTERN.match(forecast_date):
            return None, "预测日期格式不正确，应为 YYYY-MM-DD"
        # 先把所有数值校验通过再落库，避免半张单写进去后报失败。
        numeric_values: dict[str, str | None] = {}
        for field in NUMERIC_FIELDS:
            cleaned, error = _number_text(values.get(field) if field in values else None)
            if error:
                return None, f"{field}{error}"
            numeric_values[field] = cleaned

        rows = store.rows(MODULE)
        entry = self._find_by_number(number)
        if entry is None:
            entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
            entry["预测单号"] = number
            entry["abnormal"] = False
            rows.append(entry)
            message = "功率预测单已登记"
        else:
            message = f"预测单号 {number} 已存在，已更新原单内容，未新增记录"
        entry["所属场站"] = windfarm
        entry["预测日期"] = forecast_date
        entry.update(numeric_values)
        self._sync(entry, entry.get("status") if entry.get("status") in STATUS_ORDER else "待生成")
        return self._view(entry), message

    def run_action(
        self, entry_id: int, action: str, values: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any] | None, str]:
        values = values or {}
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"功率预测单 {entry_id} 不存在或已归档"
        if action not in ("生成预测", "登记偏差超标", "复核预测"):
            return None, f"动作「{action}」不属于功率预测可执行范围"

        # 动作携带的预测出力/偏差等数值先校验，任何一项不合法都不动原单。
        numeric_values: dict[str, str | None] = {}
        for field in NUMERIC_FIELDS:
            if field not in values:
                continue
            cleaned, error = _number_text(values.get(field))
            if error:
                return None, f"{field}{error}"
            numeric_values[field] = cleaned

        current = str(entry.get("status") or "待生成")
        # 必填校验全部通过后才允许写入，保证失败时原单一格不动。
        if action == "登记偏差超标" and current in ("已生成", "偏差超标"):
            deviation = numeric_values.get("预测偏差") if "预测偏差" in numeric_values else _text(entry.get("预测偏差"))
            if not deviation:
                return None, "登记偏差超标必须填写预测偏差"
        if action == "生成预测":
            if current not in ("待生成", "已生成"):
                return None, f"当前状态为「{current}」，不能生成预测"
            entry.update(numeric_values)
            target = "已生成"
            message = "预测已生成，数值已保存" if current == "待生成" else "预测此前已生成，提交内容已保存在原单上"
        elif action == "登记偏差超标":
            if current not in ("已生成", "偏差超标"):
                return None, f"当前状态为「{current}」，请先完成预测生成再登记偏差"
            entry.update(numeric_values)
            target = "偏差超标"
            entry["abnormal"] = True
            message = "偏差超标已登记，等待复核" if current == "已生成" else "偏差信息已更新，仍是同一张预测单"
        else:
            if current == "已复核":
                return self._view(entry), "该预测单已复核，无需重复操作"
            if current != "偏差超标":
                return None, f"当前状态为「{current}」，仅偏差超标的预测单可以复核"
            target = "已复核"
            message = "预测单已复核"
        self._sync(entry, target)
        return self._view(entry), message

    def _find_by_number(self, number: str) -> dict[str, Any] | None:
        for row in store.rows(MODULE):
            if str(row.get("预测单号", "")).strip() == number:
                return row
        return None

    def _sync(self, entry: dict[str, Any], status: str) -> None:
        """状态字段只认这一处：status 与中文列「预测状态」始终保持一致。"""
        entry["status"] = status
        entry["预测状态"] = status
        entry["pending"] = status in PENDING_STATUSES

    def _view(self, row: dict[str, Any]) -> dict[str, Any]:
        view = dict(row)
        view["预测状态"] = row.get("status")
        return view
