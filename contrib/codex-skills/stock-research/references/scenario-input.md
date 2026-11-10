# 可复核的情景输入

draft.py接收JSON：horizon（期限字符串）、rationale（本标的证据及假设理由）、cases（三个键悲观/基准/乐观，各有growth、pe_low、pe_high、payout）、sensitivity_pe（正倍数数组）、sensitivity_growth（大于-1的增长率数组）。growth、payout是小数比例。参数必须由当次研究确定，不带通用“合理倍数”默认值。

运行：项目Python执行本技能scripts/draft.py --bundle 数据包目录 --assumptions 参数JSON完整路径。无假设则只生成数据底稿，不生成假精确估值。已有research/目录时拒绝覆盖，使用新数据包；quant/已存在且属于本数据包则复用。

测试使用的机械压力情景不随包分发。使用者应自行创建参数JSON，并说明本标的假设依据，不得把计算测试参数当作投资估值建议。
