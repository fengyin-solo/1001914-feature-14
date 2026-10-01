<template>
  <section class="page" data-module="forecast">
    <header class="page-head">
      <div>
        <h2>功率预测管理</h2>
        <p class="page-desc">维护功率预测单，围绕预测单号、所属场站、预测日期、预测出力做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记功率预测单</button>
        <button class="btn" type="button" @click="exportRows">导出功率预测清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}{{ item.suffix }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>预测单号</span>
        <input v-model="filters.keyword" placeholder="按预测单号检索" />
      </label>
      <label class="filter-item">
        <span>所属场站</span>
        <input v-model="filters.station" placeholder="按所属场站检索" />
      </label>
      <label class="filter-item">
        <span>预测状态</span>
        <select v-model="filters.status">
          <option value="">全部状态</option>
          <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ formatCell(row, column) }}</td>
          <td class="row-actions">
            <button class="link" type="button" @click="openEdit(row)">编辑</button>
            <template v-for="action in availableActions(row)" :key="action">
              <button class="link" type="button" @click="openAction(action, row)">{{ action }}</button>
            </template>
            <span v-if="!availableActions(row).length" class="muted-text">已闭环</span>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无功率预测数据，可先登记功率预测单</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条功率预测记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <div v-if="dialog.visible" class="modal-mask" @click.self="closeDialog">
      <form class="modal" @submit.prevent="submitDialog">
        <header class="modal-head">
          <h3>{{ dialog.title }}</h3>
          <button class="btn ghost" type="button" @click="closeDialog">关闭</button>
        </header>

        <p v-if="dialog.mode === 'review'" class="modal-tip">
          复核在原预测单上进行：确认预测出力、实际出力与预测偏差无误后提交，提交后单据锁定。
        </p>
        <p v-else-if="dialog.mode === 'deviation'" class="modal-tip">
          偏差登记与后续复核落在同一份预测单上；填写实际出力后自动计算偏差百分比，超过 10% 才可登记超标。
        </p>

        <div class="form-grid">
          <label v-for="field in visibleFields" :key="field.name" class="form-item">
            <span>{{ field.label }}<em v-if="field.required">*</em></span>
            <input
              v-model="dialog.form[field.name]"
              :type="field.numeric ? 'number' : 'text'"
              :step="field.numeric ? '0.01' : undefined"
              :placeholder="`请输入${field.label}`"
              :readonly="isReadonly(field)"
            />
          </label>
        </div>

        <p v-if="dialog.error" class="error-text">{{ dialog.error }}</p>

        <footer class="modal-foot">
          <button class="btn" type="button" @click="closeDialog">取消</button>
          <button class="btn primary" type="submit" :disabled="dialog.saving">
            {{ dialog.saving ? '保存中…' : dialog.submitText }}
          </button>
        </footer>
      </form>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>
type Mode = 'create' | 'edit' | 'deviation' | 'review'

type FieldDef = {
  name: string
  label: string
  required?: boolean
  numeric?: boolean
  show: (mode: Mode) => boolean
}

const ENDPOINT = '/api/forecast'
const columns = ["预测单号", "所属场站", "预测日期", "预测出力", "实际出力", "预测偏差", "考核电量", "预测状态"]
const statuses = ["待生成", "已生成", "偏差超标", "已复核"]
// 状态 → 当前可执行的动作，与后端 ACTION_RULES 的前置状态保持一致
const ACTIONS_BY_STATUS: Record<string, string[]> = {
  "待生成": ["生成预测"],
  "已生成": ["登记偏差超标"],
  "偏差超标": ["复核预测"],
  "已复核": [],
}
const ACTION_TITLES: Record<string, string> = {
  "生成预测": "生成预测",
  "登记偏差超标": "登记偏差超标",
  "复核预测": "复核预测",
}

const fields: FieldDef[] = [
  { name: '预测单号', label: '预测单号', required: true, show: (mode) => mode === 'create' },
  { name: '所属场站', label: '所属场站', required: true, show: (mode) => mode === 'create' || mode === 'edit' },
  { name: '预测日期', label: '预测日期', required: true, show: (mode) => mode === 'create' || mode === 'edit' },
  { name: '预测出力', label: '预测出力(kWh)', required: true, numeric: true, show: () => true },
  { name: '实际出力', label: '实际出力(kWh)', numeric: true, show: (mode) => mode !== 'create' },
  {
    name: '预测偏差', label: '预测偏差(%)', numeric: true,
    show: (mode) => mode === 'edit' || mode === 'deviation' || mode === 'review',
  },
  { name: '考核电量', label: '考核电量(kWh)', numeric: true, show: (mode) => mode === 'edit' || mode === 'deviation' },
]
// 偏差登记时偏差由系统计算，复核时只做确认，两种场景都不允许手改
const READONLY_FIELDS: Record<Mode, string[]> = {
  create: [],
  edit: [],
  deviation: ['预测偏差'],
  review: ['预测出力', '实际出力', '预测偏差', '考核电量'],
}

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<{ keyword: string; station: string; status: string }>({
  keyword: '',
  station: '',
  status: '',
})
const stats = ref([
  { label: '待生成预测', value: 0, suffix: '' },
  { label: '待处理预测', value: 0, suffix: '' },
  { label: '偏差超标天数', value: 0, suffix: '' },
  { label: '预测准确率', value: 0, suffix: '%' },
])

const dialog = reactive({
  visible: false,
  saving: false,
  mode: 'create' as Mode,
  title: '',
  submitText: '',
  action: '',
  entryId: null as number | null,
  form: {} as Record<string, string>,
  error: '',
})

function availableActions(row: Row): string[] {
  return ACTIONS_BY_STATUS[String(row.status ?? '')] ?? []
}

function formatCell(row: Row, column: string): string {
  const value = row[column]
  if (value === null || value === undefined || value === '') return '—'
  return String(value)
}

function resetFilters() {
  filters.value = { keyword: '', station: '', status: '' }
  void reload()
}

function exportRows() {
  const query = new URLSearchParams()
  if (filters.value.station) query.set('station', filters.value.station)
  if (filters.value.status) query.set('status', filters.value.status)
  window.open(`${ENDPOINT}/export?${query.toString()}`, '_blank')
}

// ---------- 弹窗 ----------
function blankForm(): Record<string, string> {
  return {
    '预测单号': '',
    '所属场站': '',
    '预测日期': new Date().toISOString().slice(0, 10),
    '预测出力': '',
    '实际出力': '',
    '预测偏差': '',
    '考核电量': '',
  }
}

function formFromRow(row: Row): Record<string, string> {
  const form = blankForm()
  for (const key of Object.keys(form)) {
    const value = row[key]
    form[key] = value === null || value === undefined ? '' : String(value)
  }
  return form
}

function openCreate() {
  showDialog('create', '登记功率预测单', '保存登记', null, blankForm())
}

function openEdit(row: Row) {
  showDialog('edit', `编辑预测单 ${String(row['预测单号'] ?? '')}`, '保存修改', Number(row.id), formFromRow(row))
}

function openAction(action: string, row: Row) {
  const mode: Mode = action === '复核预测' ? 'review' : action === '登记偏差超标' ? 'deviation' : 'edit'
  showDialog(mode, `${ACTION_TITLES[action]}｜${String(row['预测单号'] ?? '')}`, action, Number(row.id), formFromRow(row), action)
}

function showDialog(
  mode: Mode,
  title: string,
  submitText: string,
  entryId: number | null,
  form: Record<string, string>,
  action = '',
) {
  dialog.visible = true
  dialog.saving = false
  dialog.mode = mode
  dialog.title = title
  dialog.submitText = submitText
  dialog.entryId = entryId
  dialog.form = form
  dialog.action = action
  dialog.error = ''
}

function closeDialog() {
  // 保存中不允许误关闭；取消时丢弃表单不影响列表
  if (dialog.saving) return
  dialog.visible = false
}

const visibleFields = computed<FieldDef[]>(() => fields.filter((field) => field.show(dialog.mode)))

function isReadonly(field: FieldDef): boolean {
  return READONLY_FIELDS[dialog.mode].includes(field.name)
}

// 偏差登记弹窗里，预测/实际出力一变就算出偏差百分比，和后端口径保持一致
watch(
  () => [dialog.mode, dialog.form['预测出力'], dialog.form['实际出力']],
  ([mode, predictedRaw, actualRaw]) => {
    if (mode !== 'deviation') return
    const predicted = Number(predictedRaw)
    const actual = Number(actualRaw)
    if (!predictedRaw || !actualRaw || !Number.isFinite(predicted) || !Number.isFinite(actual) || predicted === 0) {
      dialog.form['预测偏差'] = ''
      return
    }
    const deviation = Math.round((Math.abs(predicted - actual) / Math.abs(predicted)) * 10000) / 100
    dialog.form['预测偏差'] = String(deviation)
  },
)

async function submitDialog() {
  dialog.error = ''
  dialog.saving = true
  try {
    const payload = collectPayload()
    let response: Response
    if (dialog.mode === 'create') {
      response = await request(ENDPOINT, { method: 'POST', body: JSON.stringify({ values: payload }) })
    } else if (dialog.action) {
      // 偏差登记 / 复核：字段与动作在同一份数据、同一次请求里提交
      response = await request(`${ENDPOINT}/${dialog.entryId}/actions`, {
        method: 'POST',
        body: JSON.stringify({ values: { ...payload, action: dialog.action } }),
      })
    } else {
      response = await request(`${ENDPOINT}/${dialog.entryId}`, {
        method: 'PUT',
        body: JSON.stringify({ values: payload }),
      })
    }
    const result = await response.json().catch(() => null)
    if (!response.ok || !result?.ok) {
      // 关键：失败时只提示原因，不关闭弹窗、不清空已填内容
      dialog.error = result?.message || result?.detail || '功率预测数据未能保存，请检查填写内容后重试'
      return
    }
    dialog.visible = false
    await Promise.all([reload(), loadStats()])
  } catch (error) {
    dialog.error = error instanceof Error ? error.message : '功率预测保存失败，已保留您填写的内容'
  } finally {
    dialog.saving = false
  }
}

function collectPayload(): Record<string, string> {
  const payload: Record<string, string> = {}
  for (const field of fields.filter((item) => item.show(dialog.mode))) {
    const value = dialog.form[field.name]?.trim()
    if (value !== undefined && value !== '') payload[field.name] = value
  }
  return payload
}

// ---------- 列表与统计 ----------
function buildQuery(): string {
  const query = new URLSearchParams()
  if (filters.value.keyword) query.set('keyword', filters.value.keyword.trim())
  if (filters.value.station) query.set('station', filters.value.station.trim())
  if (filters.value.status) query.set('status', filters.value.status)
  return query.toString()
}

async function reload() {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}?${buildQuery()}`)
    if (!response.ok) {
      throw new Error('功率预测单列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    void loadStats()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '功率预测列表读取失败'
  }
}

async function loadStats() {
  try {
    const query = new URLSearchParams()
    if (filters.value.station) query.set('station', filters.value.station.trim())
    const response = await request(`${ENDPOINT}/stats?${query.toString()}`)
    if (!response.ok) return
    const data = (await response.json()) as Record<string, number>
    stats.value = [
      { label: '待生成预测', value: Number(data['待生成预测'] ?? 0), suffix: '' },
      { label: '待处理预测', value: Number(data['待复核预测'] ?? 0), suffix: '' },
      { label: '偏差超标天数', value: Number(data['偏差超标天数'] ?? 0), suffix: '' },
      { label: '预测准确率', value: Number(data['预测准确率'] ?? 0), suffix: '%' },
    ]
  } catch {
    // 统计不阻塞列表，保持上一次数字
  }
}

onMounted(reload)
</script>

<style scoped>
.muted-text { color: var(--muted); font-size: 12px; }
.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 20;
}
.modal {
  width: 560px;
  max-width: calc(100vw - 32px);
  background: #fff;
  border-radius: 10px;
  padding: 16px 18px;
  box-shadow: 0 12px 32px rgba(15, 23, 42, 0.2);
}
.modal-head { display: flex; justify-content: space-between; align-items: center; }
.modal-head h3 { margin: 0; font-size: 16px; }
.modal-tip { font-size: 12px; color: var(--muted); background: #f1f5f9; border-radius: 6px; padding: 8px 10px; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px 14px; margin: 12px 0; }
.form-item span { display: block; font-size: 12px; color: var(--muted); margin-bottom: 4px; }
.form-item em { color: #b42318; font-style: normal; margin-left: 2px; }
.form-item input,
.form-item select {
  width: 100%;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 6px 8px;
  font-size: 13px;
}
.form-item input[readonly] { background: #f1f5f9; color: var(--muted); }
.modal-foot { display: flex; justify-content: flex-end; gap: 8px; margin-top: 8px; }
.filter-item select { border: 1px solid var(--border); border-radius: 6px; padding: 6px 8px; font-size: 13px; }
</style>
