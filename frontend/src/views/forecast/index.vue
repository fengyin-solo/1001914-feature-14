<template>
  <section class="page" data-module="forecast">
    <header class="page-head">
      <div>
        <h2>功率预测管理</h2>
        <p class="page-desc">维护功率预测单，围绕预测单号、所属场站、预测日期、预测出力与预测偏差做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记功率预测单</button>
        <button class="btn" type="button" @click="exportRows">导出功率预测清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>预测单号</span>
        <input v-model="filters.keyword" placeholder="按预测单号检索" />
      </label>
      <label class="filter-item">
        <span>所属场站</span>
        <input v-model="filters.site" placeholder="按所属场站检索" />
      </label>
      <label class="filter-item">
        <span>预测日期</span>
        <input v-model="filters.date" type="date" />
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
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              class="link"
              type="button"
              :disabled="!canRun('生成预测', row)"
              :title="actionHint('生成预测', row)"
              @click="runAction('生成预测', row)"
            >
              生成预测
            </button>
            <button
              class="link"
              type="button"
              :disabled="!canRun('登记偏差超标', row)"
              :title="actionHint('登记偏差超标', row)"
              @click="openDeviation(row)"
            >
              登记偏差超标
            </button>
            <button
              class="link"
              type="button"
              :disabled="!canRun('复核预测', row)"
              :title="actionHint('复核预测', row)"
              @click="runAction('复核预测', row)"
            >
              复核预测
            </button>
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

    <div v-if="createOpen" class="modal-mask" @click.self="closeCreate">
      <form class="modal" @submit.prevent="submitCreate">
        <h3 class="modal-title">登记功率预测单</h3>
        <p class="modal-tip">预测单号相同视为同一张单，重复提交只更新原单，不会多出一行。</p>
        <label v-for="field in createFields" :key="field.name" class="modal-item">
          <span>{{ field.label }}<em v-if="field.required">*</em></span>
          <input
            v-model="createForm[field.name]"
            :type="field.type"
            step="0.01"
            :placeholder="field.placeholder"
          />
        </label>
        <p v-if="createError" class="modal-error">{{ createError }}</p>
        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="closeCreate">取消</button>
          <button class="btn primary" type="submit" :disabled="submitting">{{ submitting ? '保存中…' : '保存' }}</button>
        </div>
      </form>
    </div>

    <div v-if="deviationOpen" class="modal-mask" @click.self="closeDeviation">
      <form class="modal" @submit.prevent="submitDeviation">
        <h3 class="modal-title">登记预测偏差超标</h3>
        <p class="modal-tip">预测单 {{ deviationTarget?.['预测单号'] }}（{{ deviationTarget?.['所属场站'] }}）：填写本次复核所需的实际出力与预测偏差。</p>
        <label class="modal-item">
          <span>预测出力（万kW）</span>
          <input :value="deviationTarget?.['预测出力'] ?? ''" disabled />
        </label>
        <label v-for="field in deviationFields" :key="field.name" class="modal-item">
          <span>{{ field.label }}<em>*</em></span>
          <input v-model="deviationForm[field.name]" type="number" step="0.01" :placeholder="field.placeholder" />
        </label>
        <p v-if="deviationError" class="modal-error">{{ deviationError }}</p>
        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="closeDeviation">取消</button>
          <button class="btn primary" type="submit" :disabled="submitting">{{ submitting ? '提交中…' : '保存偏差' }}</button>
        </div>
      </form>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>
type FormState = Record<string, string>

const ENDPOINT = '/api/forecast'
const columns = ['预测单号', '所属场站', '预测日期', '预测出力', '实际出力', '预测偏差', '考核电量', '预测状态']
const statuses = ['待生成', '已生成', '偏差超标', '已复核']

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const submitting = ref(false)
const stats = ref<{ label: string; value: string | number }[]>([])
const filters = reactive<{ keyword: string; site: string; date: string; status: string }>({
  keyword: '',
  site: '',
  date: '',
  status: '',
})

const createFields = [
  { name: '预测单号', label: '预测单号', type: 'text', required: true, placeholder: '如 FORE-0004' },
  { name: '所属场站', label: '所属场站', type: 'text', required: true, placeholder: '如 北山风电场' },
  { name: '预测日期', label: '预测日期', type: 'date', required: true, placeholder: '' },
  { name: '预测出力', label: '预测出力（万kW）', type: 'number', required: false, placeholder: '选填' },
  { name: '实际出力', label: '实际出力（万kW）', type: 'number', required: false, placeholder: '选填' },
  { name: '预测偏差', label: '预测偏差（%）', type: 'number', required: false, placeholder: '选填' },
  { name: '考核电量', label: '考核电量（万kWh）', type: 'number', required: false, placeholder: '选填' },
]
const deviationFields = [
  { name: '实际出力', label: '实际出力（万kW）', placeholder: '请填写实际出力' },
  { name: '预测偏差', label: '预测偏差（%）', placeholder: '请填写预测偏差' },
  { name: '考核电量', label: '考核电量（万kWh）', placeholder: '请填写考核电量' },
]

const createOpen = ref(false)
const createError = ref('')
const createForm = reactive<FormState>({})

const deviationOpen = ref(false)
const deviationError = ref('')
const deviationTarget = ref<Row | null>(null)
const deviationForm = reactive<FormState>({})

function resetFilters() {
  filters.keyword = ''
  filters.site = ''
  filters.date = ''
  filters.status = ''
  void reload()
}

function exportRows() {
  const query = new URLSearchParams(buildQuery()).toString()
  window.open(`${ENDPOINT}/export?${query}`, '_blank')
}

function buildQuery(): Record<string, string> {
  const query: Record<string, string> = {}
  if (filters.keyword.trim()) query.keyword = filters.keyword.trim()
  if (filters.site.trim()) query.site = filters.site.trim()
  if (filters.date) query.date = filters.date
  if (filters.status) query.status = filters.status
  return query
}

function openCreate() {
  createError.value = ''
  createOpen.value = true
}

function closeCreate() {
  if (submitting.value) return
  createOpen.value = false
}

function openDeviation(row: Row) {
  deviationTarget.value = row
  deviationError.value = ''
  // 已填过的内容带回表单，便于修正后重复提交，仍然落在同一张单上。
  for (const field of deviationFields) {
    deviationForm[field.name] = row[field.name] == null ? '' : String(row[field.name])
  }
  deviationOpen.value = true
}

function closeDeviation() {
  if (submitting.value) return
  deviationOpen.value = false
  deviationTarget.value = null
}

function canRun(action: string, row: Row): boolean {
  const status = String(row['预测状态'] ?? '')
  if (action === '生成预测') return status === '待生成' || status === '已生成'
  if (action === '登记偏差超标') return status === '已生成' || status === '偏差超标'
  return status === '偏差超标'
}

function actionHint(action: string, row: Row): string {
  if (canRun(action, row)) return ''
  return `当前状态为「${row['预测状态']}」，不能执行「${action}」`
}

async function readResult(response: Response, fallback: string): Promise<string | null> {
  if (!response.ok) return fallback
  const payload = await response.json()
  return payload.ok ? null : (payload.message || fallback)
}

async function submitCreate() {
  if (submitting.value) return
  // 保存前不清空：失败时弹窗保留，已填内容原样留在输入框里。
  submitting.value = true
  createError.value = ''
  try {
    const values: Record<string, string> = {}
    for (const field of createFields) {
      values[field.name] = createForm[field.name]?.trim() ?? ''
    }
    const response = await request(ENDPOINT, {
      method: 'POST',
      body: JSON.stringify({ values }),
    })
    const reason = await readResult(response, '功率预测单未保存成功，请稍后重试')
    if (reason) {
      createError.value = reason
      return
    }
    createOpen.value = false
    for (const field of createFields) {
      createForm[field.name] = ''
    }
    await Promise.all([reload(), loadStats()])
  } catch (error) {
    createError.value = error instanceof Error ? error.message : '功率预测单保存失败'
  } finally {
    submitting.value = false
  }
}

async function submitDeviation() {
  if (submitting.value || !deviationTarget.value) return
  submitting.value = true
  deviationError.value = ''
  try {
    const values: Record<string, string> = { action: '登记偏差超标' }
    for (const field of deviationFields) {
      values[field.name] = deviationForm[field.name]?.trim() ?? ''
    }
    const response = await request(`${ENDPOINT}/${deviationTarget.value.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values }),
    })
    const reason = await readResult(response, '预测偏差未保存成功，请稍后重试')
    if (reason) {
      deviationError.value = reason
      return
    }
    deviationOpen.value = false
    deviationTarget.value = null
    await Promise.all([reload(), loadStats()])
  } catch (error) {
    deviationError.value = error instanceof Error ? error.message : '预测偏差保存失败'
  } finally {
    submitting.value = false
  }
}

async function runAction(action: string, row: Row) {
  if (!canRun(action, row)) {
    errorMessage.value = actionHint(action, row)
    return
  }
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const reason = await readResult(response, '功率预测动作未生效，请稍后重试')
    if (reason) {
      errorMessage.value = reason
      return
    }
    await Promise.all([reload(), loadStats()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '功率预测操作失败'
  }
}

async function loadStats() {
  try {
    const response = await request(`${ENDPOINT}/stats`)
    if (!response.ok) return
    const payload = await response.json()
    stats.value = Object.entries(payload).map(([label, value]) => ({
      label,
      value: value as string | number,
    }))
  } catch {
    // 统计读取失败不打断列表操作，下一次刷新再取。
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(buildQuery()).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('功率预测单列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '功率预测列表读取失败'
  }
}

onMounted(() => {
  void reload()
  void loadStats()
})
</script>

<style scoped>
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
  width: 460px;
  max-width: calc(100vw - 32px);
  background: #fff;
  border-radius: 10px;
  padding: 18px 20px;
}
.modal-title { margin: 0; font-size: 16px; }
.modal-tip { margin: 6px 0 12px; color: var(--muted); font-size: 12px; }
.modal-item { display: block; margin-bottom: 10px; font-size: 13px; }
.modal-item span { display: block; margin-bottom: 4px; color: var(--muted); font-size: 12px; }
.modal-item input { width: 100%; padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; }
.modal-item input:disabled { background: #f1f5f9; color: var(--muted); }
.modal-item em { color: #b42318; font-style: normal; margin-left: 2px; }
.modal-error { color: #b42318; font-size: 12px; margin: 4px 0 8px; }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }
.link:disabled { color: #94a3b8; cursor: not-allowed; }
</style>
