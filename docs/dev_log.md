# 2026/5/11
- 设计project plan
- build后端FASTAPI基础框架
- build前端基础框架
- 实现本地数据库的setup
- 实现Alembic的配置
- 设计 jobs 的database schema


# 2026/5/12
- 添加 jobs 的status api
- 完成Milestone 1
- 加入httpx，允许backend访问SEC
- 创建companies (公司信息), filings (公司最近的SEC文件), sec_response_cache (作为cache减少重复搜索的时间) 表格
- 实现SEC response cache service，减少重复请求，提高稳定性。
- 完成全部的Milestone 2

# 2026/5/14
- 创建 filing_document table
    - 用来记录某个filing的原始主文档是否已经下载，以及下载在哪里
- 创建filing_section table
    - 储存从HTML提取出来的section，例如：Risk Factor，MD&A等信息
- 创建document_chunk table
- 本地配有cache，当重复请求下载HTML时自动读取。
- 选择selectolax做HTML清理，然后用sec-parser作为semantic parser处理10-Q数据。
- 10-K，8-K数据额外自己写代码处理。
- 。。。


# 2026/5/15
- Milestone 3 change 7 开始实现 text-only chunking。
- 新增 section-bounded document chunk creation：按段落优先、token 上限兜底拆分，并保存 offset/hash/citation metadata。
- 暂时不做 table-aware chunking；`inscriptis` 转出的表格文本先按普通文本进入 chunks，结构化财务数据留给 XBRL。
- 修复 re-extract sections 时旧 chunks 可能阻塞 `filing_sections` 删除的问题：processing 重新解析前会先清理旧 chunks，并新增 migration 让 `document_chunks.section_id` 对 section delete cascade。
- 完成 Milestone 3 change 8 的 read API：新增 `GET /filings/{filing_id}/sections` 和 `GET /filings/{filing_id}/chunks`，供 Filing Explorer 读取 parsed sections/chunks。
- 开始 Milestone 3 change 9：前端 Filing Explorer。新增 ticker entry point、filings 列表、process/reprocess 操作、sections/chunks 浏览和 citation metadata 展示。
- 修正 Filing Explorer 的 processing 状态展示：不再把旧的 `succeeded` job 等同于 chunked，新增 `sections only` / `no chunks` / `chunked N` 状态，并在 process 后轮询 job、自动刷新当前 filing 的 sections/chunks。
- 优化table data的处理，确保表格数据放在同一个chunk内
- 优化text chunking

# 2026/5/16
- 发现chunking效果依旧不佳
- 选择使用sec2md库进行财报的处理。
- 加入sec2md作为10-K、10-Q、8-K HTML解析和chunking工具
- 新增filing_documents、filing_sections、document_chunks表格
- 实现filing_parse job，支持下载primary document、缓存raw HTML、保存annotated HTML、提取sections和chunks
- 新增/filings相关API，用于触发解析、读取sections和chunks
- 将前端从health check页面升级为Filing Explorer，可加载公司、查看filings、触发解析并浏览sections/chunks
- 完成milestone3