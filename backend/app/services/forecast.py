"""功率预测业务规则：状态流转、字段校验与筛选口径都收在这里。

同一份预测单（按预测单号唯一）在登记、生成、偏差登记、复核各环节之间复用，
重复提交是更新而不是新增；预测偏差与复核结论都落在同一条记录上。
"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "forecast"
REQUIRED_FIELDS = ["预测单号", "所属场站", "预测日期", "预测出力"]
NUMERIC_FIELDS = ["预测出力", "实际出力", "预测偏差", "考核电量"]
STATUS_ORDER = ["待生成", "已生成", "偏差超标", "已复核"]
FINAL_STATUS = STATUS_ORDER[-1]
ABNORMAL_STATUS = "偏差超标"
# 每个动作允许的前置状态；不在表里的动作不允许执行
ACTION_RULES: dict[str, dict[str, Any]] = {
    "生成预测": {"from": {"待生成"}, "to": "已生成"},
    "登记偏差超标": {"from": {"已生成", "偏差超标"}, "to": ABNORMAL_STATUS},
    "复核预测": {"from": {"偏差超标"}, "to": FINAL_STATUS},
}
DEVIATION_LIMIT = 10.0  # 预测偏差百分比绝对值超过该值即视为超标


class ForecastService:
    # ---------- 查询 ----------
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        station: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("预测单号", ""))]
        if station:
            rows = [row for row in rows if station in str(row.get("所属场站", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def stats(self, *, station: str | None = None) -> dict[str, int | float]:
        rows, _ = self.list_entries(station=station, page=1, size=10000)
        pending = sum(1 for row in rows if row.get("pending"))
        abnormal = sum(1 for row in rows if row.get("status") == ABNORMAL_STATUS)
        scored = [row for row in rows if self._accuracy(row) is not None]
        accuracy = (
            round(sum(self._accuracy(row) or 0.0 for row in scored) / len(scored), 2)
            if scored
            else 0.0
        )
        return {
            "待生成预测": sum(1 for row in rows if row.get("status") == "待生成"),
            "待复核预测": pending,
            "偏差超标天数": abnormal,
            "预测准确率": accuracy,
        }

    # ---------- 登记 / 保存 ----------
    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """登记预测单。预测单号唯一：同一单号重复提交是更新同一份数据，不会多出一行。"""
        message = self._validate(values)
        if message:
            return None, message
        rows = store.rows(MODULE)
        number = str(values.get("预测单号")).strip()
        entry = self._find_by_number(rows, number)
        if entry is None:
            entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
            rows.append(entry)
        self._apply_fields(entry, values)
        if "status" not in entry:
            entry["status"] = STATUS_ORDER[0]
        self._sync_flags(entry)
        return entry, ""

    def update_entry(self, entry_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """保存对同一份预测单的字段修改；已复核的单据锁定，需要先说明而不是静默丢弃。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"功率预测单 {entry_id} 不存在或已归档"
        if entry.get("status") == FINAL_STATUS:
            return None, "该预测单已复核并锁定，如需调整请先发起复核变更"
        message = self._validate({**entry, **values})
        if message:
            return None, message
        self._apply_fields(entry, values)
        # 仅当预测/实际出力本次被修改、且用户没有显式登记偏差时才重算，避免覆盖手工登记值
        if (
            ("预测出力" in values or "实际出力" in values)
            and "预测偏差" not in values
        ):
            self._refresh_deviation(entry)
        self._sync_flags(entry)
        return entry, ""

    # ---------- 动作流转 ----------
    def run_action(
        self, entry_id: int, action: str, values: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"功率预测单 {entry_id} 不存在或已归档"
        rule = ACTION_RULES.get(action)
        if rule is None:
            return None, f"动作「{action}」不属于功率预测可执行范围"
        current = str(entry.get("status") or "")
        if current not in rule["from"]:
            allowed = "、".join(sorted(rule["from"]))
            return None, f"当前状态为「{current}」，仅{allowed}的预测单可执行{action}"

        # 动作与字段更新落在同一份数据、同一次提交里：保存失败则状态也不动。
        updates = {k: v for k, v in (values or {}).items() if str(v or "").strip() != ""}
        if updates:
            message = self._validate({**entry, **updates})
            if message:
                return None, message
            self._apply_fields(entry, updates)

        if action == "登记偏差超标":
            message = self._refresh_deviation(entry, enforce_abnormal=True)
            if message:
                return None, message

        entry["status"] = rule["to"]
        self._sync_flags(entry)
        return entry, f"功率预测单已{action}"

    # ---------- 内部工具 ----------
    def _validate(self, values: dict[str, Any]) -> str:
        missing = [
            field
            for field in REQUIRED_FIELDS
            if not str(values.get(field) or "").strip()
        ]
        if missing:
            return f"缺少必填字段：{'、'.join(missing)}"
        for field in NUMERIC_FIELDS:
            raw = values.get(field)
            if raw is not None and str(raw).strip() != "":
                try:
                    float(str(raw).strip())
                except ValueError:
                    return f"「{field}」必须是数字，当前填写的「{raw}」无法保存"
        return ""

    def _apply_fields(self, entry: dict[str, Any], values: dict[str, Any]) -> None:
        for field in REQUIRED_FIELDS[:3]:
            if field in values:
                entry[field] = str(values[field]).strip()
        for field in NUMERIC_FIELDS:
            raw = values.get(field)
            if raw is None or str(raw).strip() == "":
                continue
            value = float(str(raw).strip())
            entry[field] = int(value) if value.is_integer() else round(value, 2)
        # 列表展示列与内部状态始终是同一份，避免看到旧值
        self._sync_flags(entry)

    def _refresh_deviation(self, entry: dict[str, Any], *, enforce_abnormal: bool = False) -> str:
        predicted = entry.get("预测出力")
        actual = entry.get("实际出力")
        if actual is None:
            return "登记偏差超标前请先填写实际出力"
        if predicted in (None, "") or float(predicted) == 0:
            return "预测出力缺失或为 0，无法计算预测偏差"
        deviation = round(abs((float(predicted) - float(actual)) / float(predicted) * 100), 2)
        entry["预测偏差"] = int(deviation) if float(deviation).is_integer() else deviation
        if enforce_abnormal and deviation <= DEVIATION_LIMIT:
            return (
                f"预测偏差为 {deviation}%，未超过 {DEVIATION_LIMIT:g}% 限值，"
                "不能登记为偏差超标"
            )
        return ""

    def _sync_flags(self, entry: dict[str, Any]) -> None:
        status = str(entry.get("status") or STATUS_ORDER[0])
        entry["status"] = status
        entry["预测状态"] = status
        entry["pending"] = status != FINAL_STATUS
        entry["abnormal"] = status == ABNORMAL_STATUS

    def _accuracy(self, row: dict[str, Any]) -> float | None:
        deviation = row.get("预测偏差")
        if deviation is None or str(deviation).strip() == "":
            return None
        try:
            return max(0.0, 100.0 - abs(float(deviation)))
        except ValueError:
            return None

    def _find_by_number(self, rows: list[dict[str, Any]], number: str) -> dict[str, Any] | None:
        for row in rows:
            if str(row.get("预测单号") or "").strip() == number:
                return row
        return None
