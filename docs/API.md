# 接口

模块GET使用仓内样例，模块POST可传csv或rows与context，直接计算当前规则。/api/runs使用完整流水线并原子保存。每个阶段返回rows、quarantine、metrics；后续阶段还返回累计隔离。/api/upload接收multipart文件file。/api/runs/<id>/archive只导出一份带清单与证据的CSV；批次存在对账差异且未签核（已复核）时返回409 UNEXPLAINED_DIFFERENCES，签核后放行。

# 到期复核

批次号由输入摘要、有效授权集与规则版本共同决定：授权列表顺序不影响批次号，同一复核重试复用原批次。parameters.grants缺省使用内置授权；提交新授权集即另立复核批次，旧批次原样保留，新批次rebase字段记录承接批次号与逐访问裁决变化（lost_basis为失去授权依据的事件数）。非法授权集返回422且不写库。清单parameters记录实际生效的授权集，证据CSV末列supersedes携带承接批次号。
