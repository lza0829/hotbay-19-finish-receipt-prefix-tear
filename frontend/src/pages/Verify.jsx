import { useEffect, useState } from 'react'
import { api } from '../api'
import { ledgerBus } from '../bus'

export default function Verify() {
  const [inv, setInv] = useState([])
  const [runs, setRuns] = useState([])
  const [last, setLast] = useState(null)
  const [msg, setMsg] = useState('')
  const reload = () => { api.inventory().then(setInv); api.verifyRuns().then(setRuns) }
  useEffect(() => {
    reload()
    // 落账后互证漂移条同刷；同笔双落成功不应新增可解释漂移
    const off = ledgerBus.subscribe(reload)
    return off
  }, [])
  async function run() {
    setMsg('')
    try {
      const r = await api.runVerify()
      setLast(r)
      const integ = r.integrity_drifts?.length || 0
      setMsg(`互证${r.status} · 漂移 ${r.drift_count}（对象 ${r.drifts.length} · 落账完整性 ${integ}）`)
      reload()
      ledgerBus.emit({ type: 'verify-ran' })
    } catch (e) { setMsg(e.message) }
  }
  return (
    <div className="page">
      <h1>目录互证</h1>
      <p className="lede">对照湖目录对象清单与分区素材元数据，不是网盘文件浏览器。落账完整性单独把关：温层与目录前缀不一致即半截漂移，不给绿。</p>
      <button type="button" onClick={run}>跑一次互证</button>
      {msg && <p className="msg">{msg}</p>}

      {last && last.integrity_drifts?.length > 0 && (
        <div className="panel drift-banner">
          <h2>落账完整性漂移条（{last.integrity_drifts.length}）</h2>
          <ul className="plain">
            {last.integrity_drifts.map((d, i) => (
              <li key={i}><strong>{d.分区}</strong> · {d.问题} · <span className="mono">{d.明细}</span></li>
            ))}
          </ul>
        </div>
      )}

      {last && last.drifts?.length > 0 && (
        <ul className="plain">{last.drifts.map((d, i) => <li key={i}>{d.对象键} · {d.问题}</li>)}</ul>
      )}
      <h2>目录清单</h2>
      <div className="table-wrap">
        <table>
          <thead><tr><th>对象键</th><th>字节</th><th>片库间</th><th>扫描时刻</th></tr></thead>
          <tbody>{inv.map(x => <tr key={x.id}><td className="mono">{x.object_key}</td><td>{x.bytes}</td><td>{x.vault_name}</td><td>{x.scanned_at}</td></tr>)}</tbody>
        </table>
      </div>
      <h2>互证流水</h2>
      <div className="table-wrap">
        <table>
          <thead><tr><th>编号</th><th>状态</th><th>漂移数</th><th>时间</th><th>明细</th></tr></thead>
          <tbody>{runs.map(r => (
            <tr key={r.id}>
              <td>{r.id}</td>
              <td><span className={`badge ${r.status === '一致' ? 'ok' : 'err'}`}>{r.status}</span></td>
              <td>{r.drift_count}</td><td>{r.created_at}</td>
              <td className="muted mono detail">{typeof r.detail_parsed === 'string' ? r.detail_parsed : JSON.stringify(r.detail_parsed)}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>
    </div>
  )
}
