# Milestone4 potential improvement
- RAW_METRIC_TAGS 目前适合 MVP，但未来需要基于真实公司测试继续扩展。
- gross_profit 对部分公司可能缺失，尤其银行、保险等行业，这不是代码 bug，而是财务口径问题。
- _computed_match_key() 的严格匹配是对的，但可能导致 computed metrics 少
    - 未来可以修改成宽松模式
    - 严格模式：必须同 accession
    - 宽松模式：同 period + fy/fp + unit 即可