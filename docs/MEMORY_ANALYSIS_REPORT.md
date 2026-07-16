# 内存使用分析及优化方案

> 文档版本: v1.0
> 生成日期: 2026-07-16
> 适用范围: `agentinsight-researcher` 项目 (`agentinsight-agent-researcher-1` 容器)
> 数据来源: 2026-07-16 实测内存监控 + 专家团队代码审计

---

## 1. 概述

### 1.1 容器配置

| 项目 | 值 |
|------|-----|
| 容器名 | `agentinsight-agent-researcher-1` |
| 容器内存限制 | 15.43 GiB |
| 并发抓取上限 | `MAX_SCRAPER_WORKERS = 15` |
| 基线内存(空闲) | 3.982 GiB (25.80%) |
| 峰值内存 | 8.156 GiB (52.85%) |
| 请求后稳定内存 | 6.375 GiB (41.31%) |

### 1.2 问题现象

2026-07-16 11:05:45 并行发送 2 个研究请求 ("研究生产力与消费能力的关系" 和 "研究下雨跟眼泪的关系") 后,观察到以下异常现象:

1. **内存峰值过高**: 基线 3.98G → 峰值 8.16G,峰值增长 +4.18G,接近容器限制的 53%。
2. **内存泄漏**: 请求结束后 CPU 降至 0.2%,但内存稳定在 6.38G,未回落到基线 3.98G,**净泄漏 +2.4G**。
3. **CPU 与内存同步飙升**: CPU 3140% 时对应内存峰值,说明大量协程并发执行时对象激增。
4. **对象未释放**: 请求结束后 CPU 已降,但内存未降,说明对象被引用持有或 GC 无法回收。

### 1.3 实测内存数据

```
基线(空闲): 3.982 GiB (25.80%)
请求发送时间: 11:05:45 (并行发送 2 个研究请求)
```

| 时间戳 | 内存 (GiB) | 占比 | CPU | 备注 |
|--------|-----------|------|-----|------|
| 11:05:45 | - | - | - | 发送 2 个并行研究请求 |
| 11:06:05 | 6.649 | 43.09% | 2594% | 飙升 +2.67G |
| 11:06:16 | 6.423 | 41.62% | 1165% | |
| 11:06:27 | 5.586 | 36.19% | 106% | 回落 |
| 11:06:38 | 7.507 | 48.64% | 1555% | 再次飙升 |
| 11:06:49 | 7.806 | 50.58% | 1901% | |
| 11:07:01 | 6.981 | 45.23% | 2751% | |
| 11:07:13 | 5.868 | 38.02% | 101% | |
| 11:07:24 | 5.739 | 37.19% | 6% | 回落 |
| 11:07:35 | 6.131 | 39.72% | - | |
| 11:07:47 | **8.156** | **52.85%** | **3140%** | **峰值** |
| 11:07:58 | 7.525 | 48.76% | - | |
| 11:08:09 | 6.123 | 39.68% | - | |
| 11:08:20 | 6.238 | 40.42% | 0.4% | |
| 11:08:31 | 6.582 | 42.65% | - | |
| 11:08:43 | 7.953 | 51.53% | 3131% | 次峰值 |
| 11:08:54 | 6.494 | 42.08% | 0.35% | |
| 11:09:06 | 6.413 | 41.55% | - | |
| 11:09:17 ~ 11:11:54 | 6.375 | 41.31% | 0.2% | **请求结束后未释放** |

---

## 2. 内存增长曲线图

```
内存(GiB)
 8.4 │                                                              ╭──╮
 8.0 │                                                            ╭─╯  ╰╮
 7.6 │                                          ╭──╮            ╭╯     │
 7.2 │                                        ╭─╯  ╰╮         ╭╯      │
 6.8 │                                      ╭╯      ╰╮      ╭╯        │
 6.4 │  ╭──╮      ╭───────╮              ╭──╯        ╰─╮  ╭─╯         ╰───── 稳定 6.375G
 6.0 │ ╭╯  ╰─╮  ╭╯       ╰─╮          ╭─╯             ╰╮╭╯
 5.6 │╭╯      ╰──╯         ╰─╮      ╭─╯                ╰╯
 5.2 │╯                     ╰──────╯
 4.8 │
 4.4 │
 4.0 │── 基线 3.982G
 3.6 │
     └──────────────────────────────────────────────────────────────────────────►
     11:05  11:06       11:07          11:08          11:09      11:10     11:12
     发送请求  飙升回落   再飙升→峰值    次峰值→回落    稳定未释放
```

**曲线特征解读**:
- **锯齿状波动**: 11:06-11:08 期间多次飙升-回落,对应并发协程的批次执行与 GC 回收节奏。
- **阶梯式上升**: 每次回落点都高于前一次 (5.586 → 5.739 → 6.123 → 6.375),说明每轮都有不可回收的增量。
- **最终平稳**: 11:09 后完全平稳在 6.375G,CPU 降至 0.2%,排除活跃计算,确认为泄漏。

---

## 3. 基线内存构成分析

基线 3.98 GiB 是请求未运行时的常驻内存,构成如下:

| # | 组件 | 估算内存 | 说明 |
|---|------|---------|------|
| 1 | Python 解释器 + 依赖库 | ~200 MB | CPython 3.12 + 已导入三方库 |
| 2 | FastEmbed ONNX 模型 | ~200-300 MB | `bge-small-zh-v1.5` INT8,用于上下文压缩 |
| 3 | Playwright 浏览器池 | ~2 GB | 5 个 chromium 实例常驻 |
| 4 | jieba 词典 + BM25 语料缓存 | ~100 MB | 中文分词 + 倒排索引 |
| 5 | asyncpg/psycopg 连接池 | ~50 MB | PostgreSQL Checkpointer + 业务表连接 |
| 6 | AgentInsight SDK BatchSpanProcessor | ~50 MB | 跨进程 span 批量导出队列 |
| 7 | ChitchatConfigBundle (6 YAML + Jinja2) | ~50 MB | 闲聊配置模板 |
| 8 | LLMClient / Settings 等单例 | ~20 MB | 全局配置对象 |
| 9 | **Python GC 未回收对象(主要嫌疑)** | **~1.3 GB** | MCP stdio 子进程 + Playwright 内部缓存 + Redis 连接缓冲 |
| | **合计** | **~3.98 GB** | |

**关键嫌疑分析**: 1.3 GB 的 GC 未回收对象是基线优化的最大空间,主要来源:
- **MCP stdio 子进程**: stdio 类型 MCP Server 启动的子进程在配置变更后被孤立,未 `aclose`。
- **Playwright 内部缓存**: 浏览器池虽常驻,但页面上下文/CDP 会话可能未完全释放。
- **Redis 连接缓冲**: 高并发下 `redis-py` 连接池缓冲未回收。

---

## 4. 内存泄漏点详细分析

### P0 - 高危泄漏(必须修复)

#### 4.1 [P0-1] `deep_research._research_sub_query` 未关闭 searchers(最严重)

**位置**: `src/skills/researcher/deep_research.py` 行 503-604

**根因**: `_research_sub_query` 方法使用 `try/except` 包裹,但**缺少 `finally` 块**关闭 `searchers` 持有的 `httpx.AsyncClient`。每个 searcher 的 `httpx.AsyncClient` 含 TCP 连接池 + SSL 上下文 + 内部缓冲区约 5-15 MB。

**对比**: `research_conductor.py` 行 790-797 已有正确的 `try/finally` 实现,但 `deep_research.py` 未对齐。

**影响**: L9-L10 深度研究单次任务生成 35 个子查询,泄漏量 = 35 × 引擎数 × 5-15 MB = **数百 MB**。

**修改前**:
```python
async def _research_sub_query(self, sub_query, ...):
    try:
        # 搜索
        searchers = await get_searchers_async(region, self.settings, quota_cache)
        search_tasks = [s.search(...) for s in searchers]
        search_results = await asyncio.gather(*search_tasks, return_exceptions=True)
        # ... 抓取 / 压缩 / learnings 提取
        return {...}
    except Exception as e:  # noqa: BLE001
        logger.warning("DeepResearch 子查询 '%s' 失败: %s", sub_query[:50], e)
        return {...}
    # ❌ 缺少 finally 关闭 searchers
```

**修改后**:
```python
async def _research_sub_query(self, sub_query, ...):
    searchers = []
    try:
        # 搜索
        searchers = await get_searchers_async(region, self.settings, quota_cache)
        search_tasks = [s.search(...) for s in searchers]
        search_results = await asyncio.gather(*search_tasks, return_exceptions=True)
        # ... 抓取 / 压缩 / learnings 提取
        return {...}
    except Exception as e:  # noqa: BLE001
        logger.warning("DeepResearch 子查询 '%s' 失败: %s", sub_query[:50], e)
        return {...}
    finally:
        # 释放 searcher 持有的 httpx.AsyncClient (防泄漏)
        # 与 research_conductor.py 行 790-797 对齐
        for s in searchers:
            try:
                await s.close()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"searcher {s.name} close 失败 (不阻断): {e}")
```

---

#### 4.2 [P0-2] `LLMClient._session_costs` 字典无清理

**位置**: `src/llm/client.py` 行 132, 257-270, 299-301

**根因**: `cleanup_session_cost(session_id)` 方法已定义但**零调用**(全代码库 grep 仅 1 处定义,无任何调用点)。每个 session 的成本记录永久驻留 `_session_costs` 字典。

**影响**: 长期运行容器 `session_costs` 单调增长,每 session 约 1-5 KB,万级会话约 10-50 MB。

**修改前**:
```python
@dataclass
class LLMClient:
    _session_costs: dict[str, dict[str, Any]] = field(default_factory=dict, init=False)
    # ...
    def cleanup_session_cost(self, session_id: str) -> None:
        # 定义了但从未被调用
        self._session_costs.pop(session_id, None)
```

**修改后**:
```python
# 1. 在会话终结处(delete_session API / TTL 清理任务)调用:
def delete_session(session_id: str):
    # ... 删除 Checkpoint / Redis 缓存 ...
    llm_client.cleanup_session_cost(session_id)  # 新增
    cleanup_token_budget_allocator(session_id)    # 新增 (配合 P0-3)
```

---

#### 4.3 [P0-3] `token_budget._allocators` 字典无清理

**位置**: `src/llm/token_budget.py` 行 216, 245-247

**根因**: `cleanup_token_budget_allocator(session_id)` 已定义并导出至 `src/llm/__init__.py`,但**零调用**。每 session 一份 `TokenBudgetAllocator` 永久驻留。

**影响**: 每 session 约 2-10 KB,长运行容器累积。

**修复**: 同 P0-2,在会话终结处调用 `cleanup_token_budget_allocator(session_id)`。

---

#### 4.4 [P0-4] `query_classifier._inflight_locks` 字典无限增长

**位置**: `src/skills/researcher/query_classifier.py` 行 204, 422-425

**根因**: 每个唯一 query 字符串创建一个 `asyncio.Lock` 并永久驻留 `_inflight_locks` 字典,用于去重并发相同查询。Lock 使用后未移除。

**影响**: 长运行容器累积唯一 query 数量,每个 Lock 约 1 KB,万级唯一查询约 10 MB。

**修改前**:
```python
_inflight_locks: dict[str, asyncio.Lock] = {}  # 全局字典

async def classify_query(query, ...):
    cache_key = f"{user_id}:{session_id}:{query}"
    if cache_key not in _inflight_locks:
        _inflight_locks[cache_key] = asyncio.Lock()
    async with _inflight_locks[cache_key]:  # ❌ 用完不 pop
        # ... 分类逻辑 ...
```

**修改后**:
```python
async def classify_query(query, ...):
    cache_key = f"{user_id}:{session_id}:{query}"
    if cache_key not in _inflight_locks:
        _inflight_locks[cache_key] = asyncio.Lock()
    lock = _inflight_locks[cache_key]
    try:
        async with lock:
            # ... 分类逻辑 ...
    finally:
        # 引用归零后移除,避免字典无限增长
        # 注意: 需确认 lock 无其他 await 等待者
        if not lock.locked() and not lock._waiters:  # type: ignore[attr-defined]
            _inflight_locks.pop(cache_key, None)
```

---

#### 4.5 [P0-5] `WrittenContentCompressor._chunk_cache` 无上限

**位置**: `src/skills/researcher/context_manager.py` 行 712-716

**根因**: `_written_embeddings` / `_written_chunks` / `_chunk_cache` 会话级累积,仅在 `reset()` 时清理。长研究任务累积数千 chunks × 512 维 float = **数十 MB**。

**影响**: 单次深度研究任务 35 子查询 × 数十 chunks,峰值约 50-100 MB。

**修改前**:
```python
self._chunk_cache: dict[str, list[str]] = {}  # 无上限
```

**修改后**:
```python
from collections import OrderedDict

class LRUCache(OrderedDict):
    def __init__(self, max_size=500):
        super().__init__()
        self.max_size = max_size
    def __setitem__(self, key, value):
        if len(self) >= self.max_size:
            self.popitem(last=False)  # 淘汰最旧
        super().__setitem__(key, value)

self._chunk_cache: LRUCache = LRUCache(max_size=500)
```

---

#### 4.6 [P0-6] `QdrantManager._namespace_cache` 全局字典无上限

**位置**: `src/rag/qdrant_manager.py` 行 32

**根因**: 全局字典缓存 namespace 信息,无清理逻辑。多用户场景下 `namespace = {agent_id}:{user_id}` 组合数 = 用户数,无界增长。

**影响**: 每用户约 1 KB 元数据,万级用户约 10 MB。

**修复**: 加 TTL 淘汰或大小上限(推荐 `cachetools.TTLCache(maxsize=1000, ttl=3600)`)。

---

#### 4.7 [P0-7] `DomainRateLimiter._semaphores` 字典无限增长

**位置**: `src/skills/researcher/scrapers/__init__.py` 行 121-153

**根因**: 全局单例,每个新域名创建 `Semaphore(1)` 永不清理。

**影响**: 长运行容器抓取过的域名累积,每域名约 1 KB,数十万域名约数百 MB。

**修复**: 用 `TTLCache` 或定期清理空闲域名(如 30 分钟无获取请求则淘汰)。

---

#### 4.8 [P0-8] `_PooledBrowser.domain_semaphores` 字典无限增长

**位置**: `src/skills/researcher/scrapers/playwright_scraper.py` 行 91, 102-105

**根因**: 与 `DomainRateLimiter` **重复实现**域名限流,双重创建 Semaphore,双重泄漏。

**修复**: 统一到 `DomainRateLimiter` 单例,删除 `_PooledBrowser.domain_semaphores`。

---

#### 4.9 [P0-9] `MCPCoordinator._client_cache` 无限增长

**位置**: `src/skills/researcher/mcp_coordinator.py` 行 181, 194-221

**根因**: 字典只增不减,每次修改 MCP 配置产生新 key,旧 client 被孤立(含 stdio 子进程)。

**影响**: 每个孤立 client 含 stdio 子进程约 10-50 MB,数次配置变更即数百 MB。

**修复**: LRU 上限 8 + 淘汰时 `aclose` + `asyncio.Lock` 保护并发。

```python
class MCPCoordinator:
    _CACHE_MAX = 8

    async def _get_or_create_client(self, key, factory):
        async with self._cache_lock:
            if key in self._client_cache:
                self._client_cache.move_to_end(key)  # LRU
                return self._client_cache[key]
            client = await factory()
            self._client_cache[key] = client
            # 淘汰最旧
            while len(self._client_cache) > self._CACHE_MAX:
                old_key, old_client = self._client_cache.popitem(last=False)
                try:
                    await old_client.aclose()
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"淘汰 MCP client {old_key} 失败: {e}")
            return client
```

---

#### 4.10 [P0-10] `Reviewer._REVIEW_CACHE` 模块级全局字典

**位置**: `src/agents/researcher/reviewer.py` 行 58

**根因**: 跨 session 共享,仅惰性清理(访问时检查 TTL)无主动 GC,30 分钟内累积无上限。

**影响**: 高并发场景 30 分钟窗口内累积数百条 review 结果,每条约 10-50 KB。

**修复**: 改用 Redis(原生 TTL)或加 LRU + `max_size`(推荐 Redis,天然支持 TTL)。

---

#### 4.11 [P0-11] `document_loader` 文件句柄未用 context manager

**位置**: `src/skills/researcher/document_loader.py` 行 224, 235, 264

**根因**: `PdfReader` / `DocxDocument` / `Presentation` 直接接收文件路径,依赖 GC 关闭文件句柄。Windows 下 GC 时机不确定,导致句柄泄漏。

**修改前**:
```python
reader = PdfReader(path)          # ❌ 依赖 GC
doc = DocxDocument(path)          # ❌ 依赖 GC
prs = Presentation(path)          # ❌ 依赖 GC
```

**修改后**:
```python
with open(path, "rb") as f:
    reader = PdfReader(f)
    # ... 处理 ...
# f 自动关闭

with open(path, "rb") as f:
    doc = DocxDocument(f)
    # ... 处理 ...

with open(path, "rb") as f:
    prs = Presentation(f)
    # ... 处理 ...
```

---

### P1 - 中危泄漏(应修复)

#### 4.12 [P1-12] `MCPCoordinator` 缺失 `close()` 方法

**位置**: `src/skills/researcher/mcp_coordinator.py`; `server.py` 行 136-172

**根因**: `lifespan` 未清理 `MCPCoordinator`,stdio 子进程被 SIGKILL,留下僵尸进程和未关闭的管道缓冲。

**修复**: 新增 `close()` 方法遍历 `_client_cache` 调用 `aclose`;`lifespan` 退出阶段调用。

```python
# MCPCoordinator
async def close(self) -> None:
    async with self._cache_lock:
        for key, client in list(self._client_cache.items()):
            try:
                await client.aclose()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"close MCP client {key} 失败: {e}")
        self._client_cache.clear()

# server.py lifespan
async def lifespan(app: FastAPI):
    # ... 启动逻辑 ...
    yield
    # 关闭阶段
    await mcp_coordinator.close()  # 新增
```

---

#### 4.13 [P1-13] `_test_mcp_config` 测试连接后未关闭

**位置**: `src/api/mcp_routes.py` 行 237-312

**根因**: 仅 `TimeoutError` 分支关闭 client,成功路径和其他异常路径未关闭。

**修复**: `try/finally` 统一清理。

```python
async def _test_mcp_config(config):
    client = None
    try:
        client = await create_client(config)
        result = await client.get_tools()
        return result
    except TimeoutError:
        return {"error": "timeout"}
    except Exception as e:
        return {"error": str(e)}
    finally:
        if client is not None:
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001
                pass
```

---

#### 4.14 [P1-14] TaskGroup 异常导致 client 僵尸

**位置**: `src/skills/researcher/mcp_coordinator.py` 行 344-389

**根因**: `get_tools()` 失败时 client 留在缓存,后续复用必失败,且无法回收。

**修复**: TaskGroup 失败后从缓存移除并 `aclose`。

```python
async def _get_tools_with_cache(self, key):
    try:
        client = await self._get_or_create_client(key, ...)
        return await client.get_tools()
    except ExceptionGroup as eg:
        # 失败则移除并关闭
        async with self._cache_lock:
            client = self._client_cache.pop(key, None)
        if client:
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001
                pass
        raise
```

---

#### 4.15 [P1-15] `achat_stream` 异常路径未显式关闭 stream

**位置**: `src/llm/client.py` 行 732, 768, 837-846

**根因**: `except Exception` 未 `await stream.aclose()`,异常时 stream 资源泄漏。

**修复**: `try/finally` + `aclose`。

```python
async def achat_stream(self, ...):
    stream = None
    try:
        stream = await self._client.chat.completions.create(stream=True, ...)
        async for chunk in stream:
            yield chunk
    except Exception:
        if stream is not None:
            try:
                await stream.aclose()
            except Exception:  # noqa: BLE001
                pass
        raise
```

---

#### 4.16 [P1-16] httpx `.text` 全量加载后截断

**位置**: `trafilatura_scraper.py` 行 50-61; `bs_markdownify_scraper.py` 行 52-64; `beautiful_soup_scraper.py` 行 31-43

**根因**: `.text` 全量加载到 str 再截断到 5MB,峰值 = 原始响应大小(可达数十 MB)。

**修改前**:
```python
resp = await client.get(url)
text = resp.text  # ❌ 全量加载
if len(text) > MAX:
    text = text[:MAX]  # 截断后,峰值仍是原始大小
```

**修改后**:
```python
async with client.stream("GET", url) as resp:
    chunks = []
    total = 0
    async for chunk in resp.aiter_bytes():
        chunks.append(chunk)
        total += len(chunk)
        if total >= MAX:
            break  # bytes 层面截断,峰值 = MAX
    text = b"".join(chunks).decode("utf-8", errors="ignore")
```

---

#### 4.17 [P1-17] `scrape_urls` 的 `gather` 结果全量驻留

**位置**: `src/skills/researcher/scrapers/__init__.py` 行 527-554

**根因**: `asyncio.gather` 一次性返回所有结果,1000 个 URL 时峰值数 GB。

**修复**: 改用 `asyncio.as_completed` + 流式 `yield`。

```python
async def scrape_urls_streaming(urls, max_workers):
    sem = asyncio.Semaphore(max_workers)
    tasks = [asyncio.create_task(_scrape_one(u, sem)) for u in urls]
    for coro in asyncio.as_completed(tasks):
        result = await coro
        if result:
            yield result
            result = None  # 显式释放引用
```

---

#### 4.18 [P1-18] `DeepResearcher` 递归树返回值整棵树 context 累积

**位置**: `src/skills/researcher/deep_research.py` 行 285-301, 319

**根因**: 递归树所有子节点并发执行,返回时整棵树 `children` 列表在根节点累积,L10 深度时树节点数指数级。

**修复**: 改为生成器/流式或分批处理,子节点结果即时消费后释放。

---

#### 4.19 [P1-19] `image_generator` 大 base64 嵌入 Markdown

**位置**: `src/skills/researcher/image_generator.py` 行 368, 423; `report_generator.py` 行 1445

**根因**: 2 MB base64 嵌入 Markdown 后,字符串拼接 + 渲染时内存翻 3 倍至 6 MB。

**修复**: 图片 base64 写入临时文件返回 URL,Markdown 引用 `![](file:///tmp/xxx.png)`。

---

#### 4.20 [P1-20] `report_generator` 大字符串拼接 + 多次正则替换

**位置**: `src/skills/researcher/report_generator.py` 行 545-553, 1558-1658

**根因**: `sections` + `body` + `full_report` 三份副本 + 10+ 次 `re.sub`,大报告(数 MB)时峰值翻 3 倍。

**修复**: 分段处理或用 `io.StringIO` 流式拼接。

---

### P2 - 低危/优化项

| # | 位置 | 问题简述 | 修复方向 |
|---|------|---------|---------|
| 21 | `markitdown_scraper.py` 行 48-56 | `r.content` 一次性加载 Office 文档 | 流式读取 |
| 22 | `pymupdf_scraper.py` 行 111-114 | `text_parts` list + join 峰值 | 流式写入 StringIO |
| 23 | `publisher` 行 137, 213, 159 | DOCX `BytesIO` 未 close + PNG bytes 循环 | `with` 上下文 + 即时释放 |
| 24 | `publisher` 行 488-493 | WeasyPrint PDF 内存峰值 100-300 MB | 分页渲染或子进程隔离 |
| 25 | `publisher` 行 378, 429-437 | SVG 占位符列表内存翻 3 倍 | 流式生成 + 即时编码 |
| 26 | `redis_lock.py` 行 35-39 | `RedisDistributedLock` 死循环无超时 | 加最大等待时长 |
| 27 | `retriever.py` 行 72-74, 567-569 | BM25 语料全量驻留内存 | 按 namespace 懒加载 + LRU |
| 28 | `fastembed_client.py` 行 75, 143 | FastEmbed ONNX 模型常驻 200-300 MB | 按需加载 + 空闲卸载(可选) |
| 29 | `retriever.py` 行 77 | `_bm25_per_namespace` 多用户缓存无界 | TTLCache 限制 namespace 数 |
| 30 | `routes.py` 行 1087 | `token_usage_logs` / 后台任务 shutdown 未等待 | `asyncio.gather(*tasks)` 等待完成 |

---

## 5. 内存峰值优化方案

针对峰值 8.16G 的问题,除修复泄漏点外,还需专项降低并发峰值:

### 5.1 抓取层流式化(P1-16 + P1-17)

将 3 个 scraper 的 `httpx.get().text` 改为 `client.stream()` bytes 层截断,并将 `scrape_urls` 改为 `as_completed` 流式 yield。预期单次 1000 URL 抓取峰值从 **数 GB → 数百 MB**。

### 5.2 深度研究树结果流式化(P1-18)

`DeepResearcher` 递归树改为子节点结果即时消费后释放,避免整棵树在根节点累积。预期 L10 任务峰值降低 **300-500 MB**。

### 5.3 图片/报告生成内存优化(P1-19 + P1-20)

- 图片 base64 写临时文件,Markdown 引用 URL,避免 3 倍放大。
- 报告生成用 `io.StringIO` 流式拼接,避免 3 份副本。

预期单次报告生成峰值降低 **100-200 MB**。

### 5.4 并发度上限收紧

当前 `MAX_SCRAPER_WORKERS=15`,在 15.43 GiB 容器下偏高。建议:
- L9-L10 深度研究期间临时降至 `MAX_SCRAPER_WORKERS=8`。
- 子查询并发数从 35 限制为分批 10-15。

### 5.5 主动 GC 触发

在深度研究节点边界、报告生成完成后,主动触发 `gc.collect()`(仅限长任务边界,避免频繁 STW)。

---

## 6. 修复优先级矩阵

| 优先级 | 数量 | 预期收益 | 修复难度 | 建议时限 |
|--------|------|---------|---------|---------|
| **P0 高危** | 11 项 | 基线降 1.5-2G,泄漏止血 | 低-中 | **1-2 周内** |
| **P1 中危** | 9 项 | 峰值降 1-2G,资源回收 | 中 | **2-4 周内** |
| **P2 低危** | 10 项 | 长尾优化,稳定性提升 | 中-高 | **按需迭代** |

### P0 修复顺序建议(按性价比)

1. **P0-1** `deep_research` searchers 关闭 — 单点收益最大(数百 MB),改动最小(加 finally)
2. **P0-9 + P0-12** MCP client 缓存 LRU + close — 解决基线 1.3G 嫌疑的主要来源
3. **P0-2 + P0-3** session_costs / token_budget 清理 — 调用点统一,一次改动覆盖
4. **P0-7 + P0-8** 域名 Semaphore 统一 + 清理 — 消除重复实现
5. **P0-4 + P0-5 + P0-6** 各类缓存 LRU/TTL — 模式统一
6. **P0-10** Reviewer 缓存改 Redis — 配合现有 Redis 基础设施
7. **P0-11** document_loader context manager — Windows 稳定性

---

## 7. 预期优化效果

| 指标 | 当前 | P0 修复后 | P0+P1 修复后 | P0+P1+P2 修复后 |
|------|------|----------|-------------|----------------|
| **基线内存(空闲)** | 3.98 G | **2.5-3.0 G** | 2.2-2.5 G | 2.0-2.2 G |
| **峰值内存(并发)** | 8.16 G | 6.5-7.0 G | **5.0-5.5 G** | 4.5-5.0 G |
| **请求后稳定值** | 6.38 G(泄漏) | **3.0-3.5 G**(回落基线) | 2.5-3.0 G | 2.2-2.5 G |
| **净泄漏量** | +2.4 G | **<100 MB**(基本止血) | <50 MB | <20 MB |
| **容器内存利用率** | 52.85%(峰值) | ~45% | ~36% | **~32%** |

### 关键预期

1. **P0 修复后**:基线从 3.98G 降至 2.5-3.0G(主要来自 MCP 子进程回收 + searchers 关闭),**泄漏基本止血**(稳定值回落到接近基线)。
2. **P0+P1 修复后**:峰值从 8.16G 降至 5.0-5.5G(主要来自抓取流式化 + 树结果流式化),容器内存利用率降至 36%。
3. **P0+P1+P2 修复后**:基线进一步降至 2.0-2.2G,峰值 4.5-5.0G,容器内存利用率 32%,**为多 Agent 并行预留充足空间**。

---

## 8. 验证方法

### 8.1 内存监控脚本

部署后使用以下命令持续监控,复现 2026-07-16 的测试场景(2 个并行研究请求):

```bash
# 每 5 秒采样容器内存与 CPU,记录到 CSV
while true; do
  ts=$(date +"%Y-%m-%d %H:%M:%S")
  stats=$(docker stats agentinsight-agent-researcher-1 --no-stream --format "{{.MemUsage}},{{.MemPerc}},{{.CPUPerc}}")
  echo "$ts,$stats" >> /tmp/mem_monitor.csv
  sleep 5
done
```

### 8.2 泄漏验证(基线回归测试)

1. **启动容器后**记录基线内存 `M_baseline_0`。
2. **发送 N 轮**(建议 N=10)2 并行研究请求,每轮记录请求前/峰值/请求后稳定值。
3. **断言**:第 N 轮请求后稳定值 `M_stable_N` 应满足:
   - `M_stable_N - M_baseline_0 < 200 MB`(P0 修复后)
   - `M_stable_N - M_stable_1 < 50 MB`(轮次间增量趋近 0,证明无累积泄漏)

### 8.3 峰值验证

1. 发送 L10 深度研究请求(35 子查询)。
2. 记录峰值内存 `M_peak`。
3. **断言**: `M_peak < 6.0 GiB`(P0+P1 修复后,容器限制 15.43 GiB 的 39%)。

### 8.4 单元测试补充

为每个修复点补充单元测试,覆盖:
- P0-1: mock searcher,断言 `close()` 被调用(含异常路径)
- P0-2/P0-3: 调用 `delete_session` 后断言字典不含 `session_id`
- P0-9: 填充 9 个 client 后断言最旧被淘汰且 `aclose` 被调用
- P0-10: Redis TTL 断言(key 在 TTL 后过期)

### 8.5 GC 对象追踪(深水区)

对基线 1.3G "GC 未回收对象" 嫌疑,使用 `tracemalloc` 精确定位:

```python
import tracemalloc
tracemalloc.start(25)  # 保留 25 帧回溯

# ... 运行一轮请求 ...

snapshot = tracemalloc.take_snapshot()
top_stats = snapshot.statistics("lineno")
for stat in top_stats[:20]:
    print(stat)
```

定位到具体分配点后,补充针对性修复。

---

## 附录:修复清单索引

| 编号 | 优先级 | 文件 | 行号 | 简述 |
|------|--------|------|------|------|
| P0-1 | 高 | `src/skills/researcher/deep_research.py` | 503-604 | searchers 未关闭 |
| P0-2 | 高 | `src/llm/client.py` | 132, 299 | `_session_costs` 无清理 |
| P0-3 | 高 | `src/llm/token_budget.py` | 216, 245 | `_allocators` 无清理 |
| P0-4 | 高 | `src/skills/researcher/query_classifier.py` | 204, 422 | `_inflight_locks` 无限增长 |
| P0-5 | 高 | `src/skills/researcher/context_manager.py` | 712-716 | `_chunk_cache` 无上限 |
| P0-6 | 高 | `src/rag/qdrant_manager.py` | 32 | `_namespace_cache` 无上限 |
| P0-7 | 高 | `src/skills/researcher/scrapers/__init__.py` | 121-153 | `DomainRateLimiter._semaphores` 无限增长 |
| P0-8 | 高 | `src/skills/researcher/scrapers/playwright_scraper.py` | 91, 102-105 | `_PooledBrowser.domain_semaphores` 重复实现 |
| P0-9 | 高 | `src/skills/researcher/mcp_coordinator.py` | 181, 194-221 | `_client_cache` 无限增长 |
| P0-10 | 高 | `src/agents/researcher/reviewer.py` | 58 | `_REVIEW_CACHE` 全局无主动 GC |
| P0-11 | 高 | `src/skills/researcher/document_loader.py` | 224, 235, 264 | 文件句柄未用 context manager |
| P1-12 | 中 | `src/skills/researcher/mcp_coordinator.py` + `server.py` | 136-172 | MCPCoordinator 缺 close() |
| P1-13 | 中 | `src/api/mcp_routes.py` | 237-312 | `_test_mcp_config` 未关闭 |
| P1-14 | 中 | `src/skills/researcher/mcp_coordinator.py` | 344-389 | TaskGroup 异常 client 僵尸 |
| P1-15 | 中 | `src/llm/client.py` | 732, 768, 837-846 | `achat_stream` 异常未关闭 stream |
| P1-16 | 中 | `trafilatura_scraper.py` / `bs_markdownify_scraper.py` / `beautiful_soup_scraper.py` | 50-61 / 52-64 / 31-43 | httpx `.text` 全量加载 |
| P1-17 | 中 | `src/skills/researcher/scrapers/__init__.py` | 527-554 | `scrape_urls` gather 全量驻留 |
| P1-18 | 中 | `src/skills/researcher/deep_research.py` | 285-301, 319 | 递归树返回值累积 |
| P1-19 | 中 | `src/skills/researcher/image_generator.py` + `report_generator.py` | 368, 423 / 1445 | base64 嵌入 Markdown |
| P1-20 | 中 | `src/skills/researcher/report_generator.py` | 545-553, 1558-1658 | 大字符串拼接 + 正则替换 |
| P2-21~30 | 低 | 见第 4 章表格 | - | 长尾优化项 |

---

> **下一步行动**: 按第 6 章 P0 修复顺序启动,优先完成 P0-1(searchers 关闭)与 P0-9/P0-12(MCP client 清理),这两项预计可回收基线 1.3G 嫌疑的主要部分。每项修复后运行第 8 章验证方法确认收益。
