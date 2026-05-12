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
