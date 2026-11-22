import { useEffect, useState } from 'react'
import { api } from '../api'
import { ledgerBus } from '../bus'

export default function Migrates() {
  const [parts, setParts] = useState([])
  const [jobs, setJobs] = useState([])
  const [pid, setPid] = useState('')
  const [to, setTo] = useState('冷档')
  const [msg, setMsg] = useState('')
  const [receipt, setReceipt] = useState(null)
  const [busy, setBusy] = useState(false)
  const reload = () => { api.partitions().then(setParts); api.migrates().then(setJobs) }
  useEffect(() => {
    reload()
    const off = ledgerBus.subscribe(reload)
    return off
  }, [])

  async function start() {
    setMsg(''); setReceipt(null)
    try {
      const r = await api.startMigrate({ partition_id: Number(pid), to_tier: to })
      setMsg(`迁移单 ${r.job_id} ${r.status}`)
      reload()
      ledgerBus.emit({ type: 'migrate-started' })
    } catch (e) { setMsg(e.message) }
  }

  async function finish(id, ok) {
    if (busy) return
    setBusy(true); setMsg(''); setReceipt(null)
    try {
      const r = await api.finishMigrate(id, ok)
      if (ok) {
        setReceipt(r)
        setMsg(`迁移单 ${r.job_id} → ${r.status}｜同笔双落 ${r.renamed} 段，温层=${r.tier}，前缀=${r.object_prefix}`)
      } else {
        setMsg(`迁移单 ${r.job_id} → ${r.status}｜温层与目录归属未动，已回到完成前快照`)
      }
      reload()
      // 通知分区页前缀列、互证漂移条、总览温层同刷
      ledgerBus.emit({ type: 'ledger-settled', receipt: r })
    } catch (e) { setMsg(e.message) }
    finally { setBusy(false) }
  }

  return (
    <div className="page">
      <h1>冷热迁移</h1>
      <div className="panel form row">
        <label>分区<select value={pid} onChange={e => setPid(e.target.value)}>
          <option value="">选择</option>
          {parts.filter(p => p.status === '在册').map(p => <option key={p.id} value={p.id}>{p.part_key}（{p.tier}）</option>)}
        </select></label>
        <label>目标温层<select value={to} onChange={e => setTo(e.target.value)}>
          <option>热档</option><option>温档</option><option>冷档</option>
        </select></label>
        <button type="button" onClick={start}>发起迁移</button>
      </div>
      {msg && <p className="msg">{msg}</p>}

      {receipt && (
        <div className="panel receipt">
          <h2>完成回执 · 同笔双落（迁移单 {receipt.job_id}）</h2>
          <ul className="plain">
            <li>完成态 / 迁移单终态：{receipt.status}</li>
            <li>落账后温层：{receipt.tier}</li>
            <li>落账后目录前缀：{receipt.object_prefix}</li>
            <li>同笔改名素材段：{receipt.renamed} 段（元数据段键与湖目录对象键成对切换）</li>
            <li>完成前快照：温层={receipt.snapshot.tier}｜前缀={receipt.snapshot.object_prefix}</li>
          </ul>
        </div>
      )}

      <div className="table-wrap">
        <table>
          <thead><tr><th>单号</th><th>分区</th><th>从</th><th>到</th><th>状态</th><th>当前目录前缀</th><th>备注</th><th>操作</th></tr></thead>
          <tbody>
            {jobs.map(j => (
              <tr key={j.id}>
                <td>{j.id}</td><td>{j.part_key}</td><td>{j.from_tier}</td><td>{j.to_tier}</td>
                <td><span className={`badge ${j.status === '执行中' ? 'warn' : j.status === '已完成' ? 'ok' : 'err'}`}>{j.status}</span></td>
                <td className="mono">{j.object_prefix}</td>
                <td className="muted">{j.note}</td>
                <td>{j.status === '执行中' && (
                  <>
                    <button type="button" disabled={busy} onClick={() => finish(j.id, true)}>完成</button>
                    <button type="button" className="ghost" disabled={busy} onClick={() => finish(j.id, false)}>失败回滚</button>
                  </>
                )}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
