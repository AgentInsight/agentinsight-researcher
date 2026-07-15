// lib/cache.ts
/**
 * 通用 TTL + LRU 缓存
 *
 * 用途:
 * - 替代模块级 Map 永不清理的缓存 (mcpCache / reportCache)
 * - TTL 控制过期, maxSize 控制 LRU 驱逐, 防止内存无限增长
 *
 * 设计:
 * - 泛型 K, V 支持任意键值类型
 * - get 命中时校验 TTL, 过期则删除并返回 undefined
 * - set 超过 maxSize 时按 ts 升序删除最旧条目 (LRU 近似)
 * - invalidate 用于显式失效单条 (保存/删除后强制下次刷新)
 * - clear 用于整体清空 (登出/切换场景)
 */
export class TTLCache<K, V> {
  private map = new Map<K, { data: V; ts: number }>();

  constructor(
    private readonly ttl: number,
    private readonly maxSize: number,
  ) {}

  get(key: K): V | undefined {
    const entry = this.map.get(key);
    if (!entry) return undefined;
    if (Date.now() - entry.ts > this.ttl) {
      this.map.delete(key);
      return undefined;
    }
    return entry.data;
  }

  set(key: K, data: V): void {
    // 已存在则先删除, 保证后续 Map 插入顺序反映最新使用
    if (this.map.has(key)) {
      this.map.delete(key);
    } else if (this.map.size >= this.maxSize) {
      // LRU 近似: 删除 ts 最旧的条目
      let oldestKey: K | null = null;
      let oldestTs = Infinity;
      for (const [k, v] of this.map.entries()) {
        if (v.ts < oldestTs) {
          oldestTs = v.ts;
          oldestKey = k;
        }
      }
      if (oldestKey !== null) this.map.delete(oldestKey);
    }
    this.map.set(key, { data, ts: Date.now() });
  }

  has(key: K): boolean {
    const entry = this.map.get(key);
    if (!entry) return false;
    if (Date.now() - entry.ts > this.ttl) {
      this.map.delete(key);
      return false;
    }
    return true;
  }

  invalidate(key: K): void {
    this.map.delete(key);
  }

  clear(): void {
    this.map.clear();
  }
}
