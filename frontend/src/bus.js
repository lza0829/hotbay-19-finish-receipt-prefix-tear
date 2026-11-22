// 全局落账事件总线：一次完成/失败落账后，迁移页、分区页（前缀列）、
// 互证（漂移条）、总览（温层/未对齐）等同刷，禁止只刷完成按钮。
const listeners = new Set()

export const ledgerBus = {
  emit(payload) {
    listeners.forEach((fn) => {
      try { fn(payload) } catch { /* 单个订阅者异常不影响其余刷新 */ }
    })
  },
  subscribe(fn) {
    listeners.add(fn)
    return () => listeners.delete(fn)
  },
}
