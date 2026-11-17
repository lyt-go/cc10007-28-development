import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/recofuse'
const columns = [
  "熔断编号",
  "策略编号",
  "推荐场景",
  "监控数据集",
  "基线版本",
  "处置结论",
  "推荐可用性",
  "最后可用召回结果",
  "处理批次"
]
const actions = [
  "提交熔断复核",
  "确认生效",
  "归档明细"
]
const statuses = [
  "草稿",
  "熔断复核中",
  "已生效",
  "已归档"
]
const stats = [
  {
    "label": "熔断明细总数",
    "value": 0
  },
  {
    "label": "已生效明细",
    "value": 0
  },
  {
    "label": "复核中明细",
    "value": 0
  }
]

type Row = Record<string, string | number | null>

export default function RecofuseView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [noticeMessage, setNoticeMessage] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const filterFields = useMemo(() => columns.slice(0, 3), [])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('推荐策略熔断列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '推荐策略熔断列表读取失败')
    }
  }, [filters])

  useEffect(() => {
    void reload()
  }, [reload])

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    setNoticeMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      if (!response.ok) {
        throw new Error('推荐策略熔断动作未生效，请稍后重试')
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '推荐策略熔断操作失败')
    }
  }

  async function computeFuse() {
    setErrorMessage('')
    setNoticeMessage('')
    const code = window.prompt('输入要计算熔断的策略编号', 'RECO-0001')
    if (!code) {
      return
    }
    try {
      const response = await request(`${ENDPOINT}/compute`, {
        method: 'POST',
        body: JSON.stringify({ values: { '策略编号': code } }),
      })
      const payload = await response.json()
      if (!response.ok || !payload.ok) {
        throw new Error(payload.message ?? payload.detail ?? '推荐策略熔断计算未生效')
      }
      setNoticeMessage(payload.message ?? '推荐策略熔断计算完成')
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '推荐策略熔断计算失败')
    }
  }

  return (
    <section className="page" data-module="recofuse">
      <header className="page-head">
        <div>
          <h2>推荐策略熔断管理</h2>
          <p className="page-desc">读取漂移监控数据集计算策略熔断结果，联动推荐场景可用性并投递数据漂移待处理明细。</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => void computeFuse()}>
            计算策略熔断
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出推荐策略熔断清单
          </button>
        </div>
      </header>

      <div className="stat-row">
        {stats.map((item) => (
          <article key={item.label} className="stat-card">
            <span className="stat-label">{item.label}</span>
            <strong className="stat-value">{item.value}</strong>
          </article>
        ))}
      </div>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        {filterFields.map((field) => (
          <label key={field} className="filter-item">
            <span>{field}</span>
            <input
              value={filters[field] ?? ''}
              placeholder={`按${field}检索`}
              onChange={(event) => setFilters((prev) => ({ ...prev, [field]: event.target.value }))}
            />
          </label>
        ))}
        <label className="filter-item">
          <span>处理批次</span>
          <input
            value={filters.batch ?? ''}
            placeholder="按处理批次核对"
            onChange={(event) => setFilters((prev) => ({ ...prev, batch: event.target.value }))}
          />
        </label>
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => setFilters({})}>重置条件</button>
      </form>

      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)}>
              {columns.map((column) => <td key={column}>{row[column] ?? '—'}</td>)}
              <td className="row-actions">
                {actions.map((action) => (
                  <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                    {action}
                  </button>
                ))}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 1}>暂无推荐策略熔断数据，可先计算策略熔断</td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>共 {total} 条推荐策略熔断记录</span>
        {noticeMessage && <span className="notice-text">{noticeMessage}</span>}
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
