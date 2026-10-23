# 大数据性能测量（2026-10-23）

数据全部在 `TemporaryDirectory` 中生成并在结束时删除，程序临时替换并最终恢复 `config.DATA_DIR / DB_PATH / MATERIALS_DIR`。没有读取或写入真实学习数据库，没有调用模型。数据固定为 5,000 篇笔记、10,000 张卡片、1,000 份资料及其合成原件、10,000 个资料片段、10,000 条双链；笔记正文合计 12,850,000 字符，每篇约 2,570 字符。节点采用确定性 UUID，约四分之一第一条边使用旧标题链接，其余使用稳定 ID。

环境：Windows 11，Python 3.13.15，SQLite 3.50.4。通过 FastAPI `TestClient` 测量完整 HTTP 请求墙钟时间，包含路由、数据库连接、SQL、正文处理、JSON 序列化和中间件。每个端点记录首次请求，随后 20 次请求取中位数及最近秩 P95。响应大小为未压缩 JSON 字节数。**首次请求不是操作系统冷缓存测试**：没有清空 OS 文件缓存；不同端点共享同一合成数据库。开发环境另有测试和构建活动，跨运行耗时会波动，不能当作专用机器的性能保证。没有测真实浏览器绘制、D3 布局耗时或桌面 WebView 交互延迟。

## 修改前

原始样本保存在 [performance-baseline.json](performance-baseline.json)。

| 请求 | 首次 ms | 暖中位 ms | 暖 P95 ms | 响应字节 | 项数 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 全部笔记 | 2,637 | 2,283 | 2,984 | 2,070,001 | 5,000 |
| 单学科笔记 | 217 | 231 | 266 | 207,001 | 500 |
| 搜索 common | 1,338 | 1,700 | 2,119 | 11,632 | 30 |
| 搜索 needle0042 | 360 | 418 | 506 | 10,333 | 30 |
| 搜索 common retention | 1,228 | 1,373 | 1,908 | 11,722 | 30 |
| 全部图谱 | 1,952 | 1,685 | 1,997 | 2,065,050 | 5,000 节点 |
| 单学科图谱 | 565 | 371 | 600 | 108,551 | 500 节点 |

全列表及全图的 CPU 和传输体积均明显超出小数据场景；宽泛搜索也超过一秒。这些实测触发了以下局部修改，没有引入全文检索服务、后台索引服务器或新基础设施。

## 修改及复测

- 笔记列表新增分页接口，每页最多处理选中笔记的正文、摘要和相关计数；界面每次取 40 篇。全局补全使用轻量 `/api/notes/index`，旧 `/api/notes` 保持兼容。
- 搜索先以有界 Top K 选择最终结果，再为可见结果生成高亮片段。保留标题权重、更新时间次序、相同得分的原类别/行次序及每份资料最佳片段的规则。六组行为测试对照穷举排序，验证多词、大小写、中文、字面 `%_`、学科筛选和资料去重。
- 搜索弹窗保留既有 160 ms 防抖，新增 AbortController 和请求代次检查，旧慢响应及关闭后的响应无法覆盖新结果。两组测试覆盖传输忽略取消的情况。
- 大图默认中心局部视图；一层或两层 BFS 筛选输出。后端仍读取所有笔记并解析关系，但只为最终可见节点生成摘要、序列化。没有声称局部图是数据库局部索引查询。

最终汇总原始样本在 [performance-final.json](performance-final.json)，按两个实际执行批次分别保存，没有将分批执行伪称为单次隔离运行；原始文件为 [performance-optimized.json](performance-optimized.json) 和 [performance-local-graph.json](performance-local-graph.json)：

| 请求 | 首次 ms | 暖中位 ms | 暖 P95 ms | 响应字节 | 项数 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 笔记分页 limit=40 | 178 | 218 | 286 | 16,622 | 40 |
| 笔记标题索引 | 42 | 82 | 107 | 475,001 | 5,000 |
| 搜索 common | 1,214 | 1,377 | 1,586 | 11,632 | 30 |
| 搜索 needle0042 | 674 | 496 | 654 | 10,333 | 30 |
| 搜索 common retention | 1,068 | 1,093 | 1,545 | 11,722 | 30 |
| 中心两层图谱 | 484 | 599 | 794 | 4,539 | 13 节点 |
| 自动中心一层图谱 | 675 | 182 | 217 | 1,627 | 5 节点 |

分页相对全量接口减少约 99.2% 响应体，本次暖中位降低约 90%；标题索引保持全局补全能力。中心一层和两层图谱将响应从约 2 MB 降为 1.6 KB / 4.5 KB；后端全量关系解析仍是规模上限。

搜索最新宽泛/多词查询中位较基线降低约 19% / 20%，**仍未达到一秒目标**；选择性查询没有改善。另一次运行 common 中位曾为 326 ms、P95 为 1,244 ms，保留于 [performance-search-optimized.json](performance-search-optimized.json)，说明共享开发环境波动较大，不能据该单次运行宣称稳定 81% 加速。其 cProfile 显示仅生成最终 30 个片段；剩余热点为数据库扫描、结果迭代和正文 Unicode 小写转换。后续若真实大数据使用持续缓慢，可在专用稳定环境复测后考虑持久化文本索引；本次没有为此加入复杂基础设施。

## 复现

在 `backend` 中运行，输出文件是唯一保留物；数据库和合成资料始终位于临时目录：

```powershell
.venv/Scripts/python.exe scripts/benchmark.py --output ../docs/performance-new.json
.venv/Scripts/python.exe scripts/benchmark.py --only search_common,search_selective,search_multiple,extra_0,extra_1 --extra-path '/api/notes/page?limit=40' --extra-path '/api/notes/index' --output ../docs/performance-new-search-list.json
.venv/Scripts/python.exe scripts/benchmark.py --only extra_0,extra_1 --extra-path '/api/graph?center_id=b3221b21-51d2-51f9-83d7-bc9b5b072edb&depth=2' --extra-path '/api/graph?auto_local=true' --output ../docs/performance-new-local-graph.json
```

可附加 `--profile-search` 记录直接搜索的 cProfile。第一次图谱测量也包括首次旧链接绑定的持久化，暖测量复用已绑定关系；当前新代码不提供“运行原代码”开关，基线 JSON 是修改前实际保存的结果。
