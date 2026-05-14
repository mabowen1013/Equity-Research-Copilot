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
- 
