import { useEffect, useState } from 'react'
import { api } from '../api'
import { ledgerBus } from '../bus'

export default function Home() {
  const [o, setO] = useState(null)
  const [err, setErr] = useState('')
  const load = () => api.overview().then(setO).catch(e => setErr(e.message))
  useEffect(() => {
    load()
    // 完成落账后温层卡与未对齐提示同刷；同笔双落下未对齐恒为 0
    const off = ledgerBus.subscribe(load)
    return off
  }, [])
  if (err) return <p className="err">{err}</p>
  if (!o) return <p className="muted">加载总览…</p>
  const _ok = o && o.surface_ok;
  const cards = [
    ['热档', _ok ? (o.surface_热档 ?? o.热档) : o.热档], ['温档', _ok ? (o.surface_温档 ?? o.温档) : o.温档], ['冷档', _ok ? (o.surface_冷档 ?? o.冷档) : o.冷档],
    ['迁移执行中', o.迁移执行中], ['待销毁候选', o.待销毁候选], ['目录对象', o.目录对象],
  ]
  return (
    <div className="page">
      <header className="hero">
        <p className="eyebrow">影院素材温层</p>
        <h1>热档片间</h1>
        <p className="lede">分区在册，冷热可迁，片龄到期可销毁，目录与元数据互证。温层计数仅统计温层与目录前缀已对齐的分区。</p>
      </header>

      {o.前缀未对齐 > 0 && (
        <div className="panel drift-banner">
          <h2>前缀未对齐（{o.前缀未对齐}）— 未计入温层卡，禁止伪装已对齐</h2>
          <ul className="plain">
            {o.未对齐分区.map(p => (
              <li key={p.part_key}>{p.part_key} · 温层={p.tier} · 实挂前缀 <span className="mono">{p.object_prefix}</span></li>
            ))}
          </ul>
        </div>
      )}

      <div className="stat-grid">
        {cards.map(([k, v]) => (
          <div className="stat" key={k}><span>{k}</span><strong>{v}</strong></div>
        ))}
      </div>
      <section className="panel">
        <h2>片库间</h2>
        <ul className="plain">
          {o.片库间.map(v => (
            <li key={v.id}>{v.name} · 额定 {v.capacity} · {v.active ? '启用' : '停用'}</li>
          ))}
        </ul>
        {o.最近互证 && (
          <p className="muted">最近互证：{o.最近互证.status} · 漂移 {o.最近互证.drift_count} · {o.最近互证.created_at}</p>
        )}
      </section>
    </div>
  )
}
