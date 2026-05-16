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


# 2026/5/16
- 开始并实现Milestone 3核心能力
- 加入sec2md作为10-K、10-Q、8-K HTML解析和chunking工具
- 新增filing_documents、filing_sections、document_chunks表格
- 实现filing_parse job，支持下载primary document、缓存raw HTML、保存annotated HTML、提取sections和chunks
- 新增/filings相关API，用于触发解析、读取sections和chunks
- 将前端从health check页面升级为Filing Explorer，可加载公司、查看filings、触发解析并浏览sections/chunks
