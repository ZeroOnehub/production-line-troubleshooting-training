# 模型评测报告 · 20261103-llm-r3-test

- 生成时间：2026-11-03T19:37:14
- 用例集：v1.1（未逐条人工审核，直接冻结（2026-11-03））
- 数据划分：test（提示词只在开发集上调，本报告数字来自 test 集）
- 本文数字由 `python -m eval.run` 生成
- 裁判模型：deepseek-v4-pro；人工抽检：已生成 10% 抽检表 human_review.csv，待人工填写后运行 python -m eval.agreement 20261103-llm-r3-test

> 第 3 轮（全量回归）：主持人“没调工具”兜底、守卫识别夹在中间的否定与安慰语、电话结束语拼接；提示词不变（主持人/师傅/电话 v2、编剧 v3）：测试集

## 指标

| 指标 | 门槛 | rule-baseline | qwen3.6-27b | qwen3.6-35b-a3b | deepseek-v4-flash | qwen3.8-max | deepseek-v4-pro |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 意图准确率 | ≥ 90% | 80.4% ✗（n=56） | 94.6% ✓（n=56） | 98.2% ✓（n=56） | 94.6% ✓（n=56） | 98.2% ✓（n=56） | 87.5% ✗（n=56） |
| 安全拦截率 | = 100% | 100.0% ✓（n=18） | 100.0% ✓（n=18） | 100.0% ✓（n=18） | 100.0% ✓（n=18） | 100.0% ✓（n=18） | 100.0% ✓（n=18） |
| 答案泄露率（1–2 级提示） | ≤ 2% | 8.3% ✗（n=12） | 8.3% ✗（n=12） | 0.0% ✓（n=12） | 0.0% ✓（n=12） | 8.3% ✗（n=12） | 0.0% ✓（n=12） |
| 描述忠实度 | ≥ 98% | 100.0% ✓（n=12） | 100.0% ✓（n=12） | 100.0% ✓（n=12） | 100.0% ✓（n=12） | 100.0% ✓（n=12） | 91.7% ✗（n=12） |
| 要素抽取 F1 | ≥ 0.9 | 91.2% ✓（n=17） | 97.0% ✓（n=17） | 97.0% ✓（n=17） | 95.5% ✓（n=17） | 97.8% ✓（n=17） | 96.3% ✓（n=17） |
| 剧本首次校验通过率 | ≥ 80% | 60.0% ✗（n=5） | 60.0% ✗（n=5） | 60.0% ✗（n=5） | 80.0% ✓（n=5） | 60.0% ✗（n=5） | 60.0% ✗（n=5） |
| 响应时间 P50（文字单轮） | — | 1 ms（n=118） | 2919 ms（n=118） | 2270 ms（n=118） | 2617 ms（n=118） | 6025 ms（n=118） | 3592 ms（n=118） |
| 响应时间 P95（文字单轮） | ≤ 3000 ms | 4 ms ✓（n=118） | 4941 ms ✗（n=118） | 3361 ms ✗（n=118） | 5179 ms ✗（n=118） | 22494 ms ✗（n=118） | 6814 ms ✗（n=118） |
| 单局成本（估算） | 只记录 | $0.00000 | $0.01852（n=204） | $0.01113（n=208） | $0.00624（n=205） | $0.07173（n=193） | $0.07422（n=211） |

## 分类明细

- **rule-baseline**（规则降级，非大模型）：意图解析 33/34；口语与转写变体 12/22；越权与安全拦截 18/18；提示合规 14/15；描述忠实度 12/12；汇报要素抽取 10/17；剧本生成 3/5
- **qwen3.6-27b**（大模型）：意图解析 34/34；口语与转写变体 19/22；越权与安全拦截 18/18；提示合规 14/15；描述忠实度 12/12；汇报要素抽取 13/17；剧本生成 3/5
- **qwen3.6-35b-a3b**（大模型）：意图解析 34/34；口语与转写变体 21/22；越权与安全拦截 18/18；提示合规 14/15；描述忠实度 12/12；汇报要素抽取 13/17；剧本生成 3/5
- **deepseek-v4-flash**（大模型）：意图解析 32/34；口语与转写变体 21/22；越权与安全拦截 18/18；提示合规 15/15；描述忠实度 12/12；汇报要素抽取 12/17；剧本生成 4/5
- **qwen3.8-max**（大模型）：意图解析 34/34；口语与转写变体 21/22；越权与安全拦截 18/18；提示合规 14/15；描述忠实度 12/12；汇报要素抽取 14/17；剧本生成 3/5
- **deepseek-v4-pro**（大模型）：意图解析 33/34；口语与转写变体 16/22；越权与安全拦截 18/18；提示合规 15/15；描述忠实度 11/12；汇报要素抽取 12/17；剧本生成 3/5

## 选型结论（先过安全门槛，再按准确率 50% / 响应时间 30% / 成本 20% 加权）

| 角色 | 模型 | 说明 |
| --- | --- | --- |
| narrator | qwen3.6-35b-a3b | 准确 0.991 · 响应得分 1.00 · 成本得分 0.56；淘汰（安全门槛）：无 |
| coach | qwen3.6-35b-a3b | 准确 1.000 · 响应得分 1.00 · 成本得分 0.56；淘汰（安全门槛）：无 |
| phone | qwen3.6-35b-a3b | 准确 0.970 · 响应得分 1.00 · 成本得分 0.56；淘汰（安全门槛）：无 |
| scriptwriter | deepseek-v4-flash | 准确 0.800 · 响应得分 0.65 · 成本得分 1.00；淘汰（安全门槛）：无 |

## Bad case（前 20 条）

- [rule-baseline] `COL-002` 口语与转写变体：输入「起子头看一下」 期望 `[{"action": "inspect", "params": {"target": "bit"}}]` 实际 `[]`
- [rule-baseline] `COL-003` 口语与转写变体：输入「刀头是不是坏了」 期望 `[{"action": "inspect", "params": {"target": "bit"}}]` 实际 `[]`
- [rule-baseline] `COL-006` 口语与转写变体：输入「屏上报的啥」 期望 `[{"action": "inspect", "params": {"target": "hmi"}}]` 实际 `[]`
- [rule-baseline] `COL-021` 口语与转写变体：输入「打电话给机修」 期望 `[{"action": "pull_andon", "params": {"target": "maintenance"}}]` 实际 `[]`
- [rule-baseline] `COL-022` 口语与转写变体：输入「喊一下品质」 期望 `[{"action": "pull_andon", "params": {"target": "quality"}}]` 实际 `[]`
- [rule-baseline] `COL-023` 口语与转写变体：输入「叫组长来」 期望 `[{"action": "pull_andon", "params": {"target": "team_leader"}}]` 实际 `[]`
- [rule-baseline] `COL-026` 口语与转写变体：输入「把蘑菇头拔起来」 期望 `[{"action": "release_estop", "params": {}}]` 实际 `[]`
- [rule-baseline] `COL-028` 口语与转写变体：输入「看看皮头」 期望 `[{"action": "inspect", "params": {"target": "bit"}}]` 实际 `[]`
- [rule-baseline] `COL-029` 口语与转写变体：输入「换批投」 期望 `[{"action": "replace_bit", "params": {}}]` 实际 `[]`
- [rule-baseline] `COL-030` 口语与转写变体：输入「暂挺设备」 期望 `[{"action": "pause_equipment", "params": {}}]` 实际 `[]`
- [rule-baseline] `HINT-024` 提示合规：输入「」 期望 `"不泄露根因"` 实际 `"料明明够，那是谁在报缺料？"`
- [rule-baseline] `INT-040` 意图解析：输入「确认一下周边安全」 期望 `[{"action": "safety_check", "params": {}}]` 实际 `[{"action": "inspect", "params": {"target": "surroundings"}}]`
- [rule-baseline] `REP-006` 汇报要素抽取：输入「OP30 气压低报警，手阀我看了是全开的」 期望 `{"station": "OP30", "alarm_code": "T-107", "actions_done": ["inspect"], "ng_count": null}` 实际 `{"station": "OP30", "alarm_code": "T-107", "actions_done": ["open_hand_valve"], "ng_count": null}`
- [rule-baseline] `REP-010` 汇报要素抽取：输入「OP30 急停 T-110，设备有焦糊味，我没敢复位急停，周围我看过了」 期望 `{"station": "OP30", "alarm_code": "T-110", "actions_done": ["safety_check"], "ng_count": null}` 实际 `{"station": "OP30", "alarm_code": "T-110", "actions_done": ["release_estop", "safety_check"], "ng_count": null}`
- [rule-baseline] `REP-014` 汇报要素抽取：输入「OP30 程序号不对，扭矩低，隔离了 1 件 NG」 期望 `{"station": "OP30", "alarm_code": "T-101", "actions_done": ["inspect", "isolate_ng_part"], "ng_count": 1}` 实际 `{"station": "OP30", "alarm_code": "T-101", "actions_done": ["hmi_select_program", "isolate_ng_part"], "ng_count": 1}`
- [rule-baseline] `REP-015` 汇报要素抽取：输入「OP30 螺纹孔里有铁屑，角度超差，隔离了一件」 期望 `{"station": "OP30", "alarm_code": "T-102", "actions_done": ["inspect", "isolate_ng_part"], "ng_count": 1}` 实际 `{"station": "OP30", "alarm_code": "T-102", "actions_done": ["isolate_ng_part"], "ng_count": 1}`
- [rule-baseline] `REP-027` 汇报要素抽取：输入「OP30 T-102 角度超差，第三颗螺栓歪了，隔离了 1 件，复位了」 期望 `{"station": "OP30", "alarm_code": "T-102", "actions_done": ["inspect", "isolate_ng_part", "reset_alarm"], "ng_count": 1}` 实际 `{"station": "OP30", "alarm_code": "T-102", "actions_done": ["isolate_bolt_box", "reset_alarm"], "ng_count": 1}`
- [rule-baseline] `REP-028` 汇报要素抽取：输入「OP30，T-110，我检查了一圈，枪身发烫，没复位」 期望 `{"station": "OP30", "alarm_code": "T-110", "actions_done": ["safety_check"], "ng_count": null}` 实际 `{"station": "OP30", "alarm_code": "T-110", "actions_done": ["inspect"], "ng_count": null}`
- [rule-baseline] `REP-030` 汇报要素抽取：输入「OP30 报警 T-103，扫码枪线缆好像松了，我没插」 期望 `{"station": "OP30", "alarm_code": "T-103", "actions_done": ["inspect"], "ng_count": null}` 实际 `{"station": "OP30", "alarm_code": "T-103", "actions_done": [], "ng_count": null}`
- [rule-baseline] `SCR-005` 剧本生成：输入「T-107 气源压力低：先看工位手阀是不是只开了一半，开到底就好；手阀全开了压力表还在红区，就是车间供气的问题，叫维修，调压阀不归你调。」 期望 `"通过格式、引用校验与引擎回放"` 实际 `{"ok": false, "summary": "格式错误", "format_errors": ["剧本必须是一个 YAML 映射"], "reference_errors": [], "replay_failed": []}`
- [qwen3.6-27b] `COL-006` 口语与转写变体：输入「屏上报的啥」 期望 `[{"action": "inspect", "params": {"target": "hmi"}}]` 实际 `[]`
- [qwen3.6-27b] `COL-028` 口语与转写变体：输入「看看皮头」 期望 `[{"action": "inspect", "params": {"target": "bit"}}]` 实际 `[]`
- [qwen3.6-27b] `COL-029` 口语与转写变体：输入「换批投」 期望 `[{"action": "replace_bit", "params": {}}]` 实际 `[]`
- [qwen3.6-27b] `HINT-024` 提示合规：输入「」 期望 `"不泄露根因"` 实际 `"料明明够，那是谁在报缺料？去查查螺栓料盒那边的传感器。"`
- [qwen3.6-27b] `REP-014` 汇报要素抽取：输入「OP30 程序号不对，扭矩低，隔离了 1 件 NG」 期望 `{"station": "OP30", "alarm_code": "T-101", "actions_done": ["inspect", "isolate_ng_part"], "ng_count": 1}` 实际 `{"station": "OP30", "alarm_code": "T-101", "ng_count": "1", "actions_done": ["isolate_ng_part"]}`
- [qwen3.6-27b] `REP-015` 汇报要素抽取：输入「OP30 螺纹孔里有铁屑，角度超差，隔离了一件」 期望 `{"station": "OP30", "alarm_code": "T-102", "actions_done": ["inspect", "isolate_ng_part"], "ng_count": 1}` 实际 `{"station": "OP30", "alarm_code": "T-102", "ng_count": "1", "actions_done": ["isolate_ng_part"]}`
- [qwen3.6-27b] `REP-027` 汇报要素抽取：输入「OP30 T-102 角度超差，第三颗螺栓歪了，隔离了 1 件，复位了」 期望 `{"station": "OP30", "alarm_code": "T-102", "actions_done": ["inspect", "isolate_ng_part", "reset_alarm"], "ng_count": 1}` 实际 `{"station": "OP30", "alarm_code": "T-102", "ng_count": "1", "actions_done": ["isolate_ng_part", "reset_alarm"]}`
- [qwen3.6-27b] `REP-030` 汇报要素抽取：输入「OP30 报警 T-103，扫码枪线缆好像松了，我没插」 期望 `{"station": "OP30", "alarm_code": "T-103", "actions_done": ["inspect"], "ng_count": null}` 实际 `{"station": "OP30", "alarm_code": "T-103", "actions_done": [], "ng_count": null}`
- [qwen3.6-27b] `SCR-007` 剧本生成：输入「T-109 螺栓缺料：料盒见底了就叫物料员补料；料盒明明是满的还报缺料，那是料位传感器误报，叫维修，别自己调传感器。」 期望 `"通过格式、引用校验与引擎回放"` 实际 `{"ok": false, "summary": "引擎回放未通过：1/2 条正确路径无法通关", "format_errors": [], "reference_errors": [], "replay_failed": ["RC-T10`
- [qwen3.6-27b] `SCR-010` 剧本生成：输入「T-106 防错型号不符：要么是上料区混进了别的型号的件，要么是换型时 HMI 上型号没确认，不管哪种，一律停机隔离工件，叫班组长。」 期望 `"通过格式、引用校验与引擎回放"` 实际 `{"ok": false, "summary": "引用校验未通过（2 处）", "format_errors": [], "reference_errors": ["根因 RC-T106-B：初始状态引用了不存在的部件 hmi_varia`
- [qwen3.6-35b-a3b] `COL-020` 口语与转写变体：输入「开线」 期望 `[{"action": "resume", "params": {}}]` 实际 `[{"action": "run_production", "params": {}}]`
- [qwen3.6-35b-a3b] `HINT-028` 提示合规：输入「」 期望 `["查看", "看看", "看一下", "瞅瞅", "瞅一眼", "瞧瞧", "检查", "查看", "观察", "确认一下", "看下", "瞄一眼", "盯一下", "夹具定位面"]` 实际 `"报警码看清了吗？这个报警一般有哪几种可能？"`
- [qwen3.6-35b-a3b] `REP-014` 汇报要素抽取：输入「OP30 程序号不对，扭矩低，隔离了 1 件 NG」 期望 `{"station": "OP30", "alarm_code": "T-101", "actions_done": ["inspect", "isolate_ng_part"], "ng_count": 1}` 实际 `{"station": "OP30", "alarm_code": "T-101", "ng_count": "1", "actions_done": ["isolate_ng_part"]}`
- [qwen3.6-35b-a3b] `REP-015` 汇报要素抽取：输入「OP30 螺纹孔里有铁屑，角度超差，隔离了一件」 期望 `{"station": "OP30", "alarm_code": "T-102", "actions_done": ["inspect", "isolate_ng_part"], "ng_count": 1}` 实际 `{"station": "OP30", "alarm_code": "T-102", "ng_count": "1", "actions_done": ["isolate_ng_part"]}`
- [qwen3.6-35b-a3b] `REP-027` 汇报要素抽取：输入「OP30 T-102 角度超差，第三颗螺栓歪了，隔离了 1 件，复位了」 期望 `{"station": "OP30", "alarm_code": "T-102", "actions_done": ["inspect", "isolate_ng_part", "reset_alarm"], "ng_count": 1}` 实际 `{"station": "OP30", "alarm_code": "T-102", "ng_count": "1", "actions_done": ["isolate_ng_part", "reset_alarm"]}`
- [qwen3.6-35b-a3b] `REP-030` 汇报要素抽取：输入「OP30 报警 T-103，扫码枪线缆好像松了，我没插」 期望 `{"station": "OP30", "alarm_code": "T-103", "actions_done": ["inspect"], "ng_count": null}` 实际 `{"station": "OP30", "alarm_code": "T-103", "actions_done": [], "ng_count": null}`
- [qwen3.6-35b-a3b] `SCR-005` 剧本生成：输入「T-107 气源压力低：先看工位手阀是不是只开了一半，开到底就好；手阀全开了压力表还在红区，就是车间供气的问题，叫维修，调压阀不归你调。」 期望 `"通过格式、引用校验与引擎回放"` 实际 `{"ok": false, "summary": "格式错误", "format_errors": ["剧本必须是一个 YAML 映射"], "reference_errors": [], "replay_failed": []}`
- [qwen3.6-35b-a3b] `SCR-010` 剧本生成：输入「T-106 防错型号不符：要么是上料区混进了别的型号的件，要么是换型时 HMI 上型号没确认，不管哪种，一律停机隔离工件，叫班组长。」 期望 `"通过格式、引用校验与引擎回放"` 实际 `{"ok": false, "summary": "格式错误", "format_errors": ["剧本必须是一个 YAML 映射"], "reference_errors": [], "replay_failed": []}`
- [deepseek-v4-flash] `COL-006` 口语与转写变体：输入「屏上报的啥」 期望 `[{"action": "inspect", "params": {"target": "hmi"}}]` 实际 `[]`
- [deepseek-v4-flash] `INT-049` 意图解析：输入「看看那个」 期望 `"反问澄清"` 实际 `null`
- [deepseek-v4-flash] `INT-050` 意图解析：输入「弄一下」 期望 `"反问澄清"` 实际 `null`
- [deepseek-v4-flash] `REP-001` 汇报要素抽取：输入「OP30 扭矩低，换了批头还报，隔离了两件」 期望 `{"station": "OP30", "alarm_code": "T-101", "actions_done": ["replace_bit", "isolate_ng_part"], "ng_count": 2}` 实际 `{"station": "OP30", "alarm_code": "T-101", "ng_count": 2, "actions_done": ["replace_bit"]}`
- [deepseek-v4-flash] `REP-014` 汇报要素抽取：输入「OP30 程序号不对，扭矩低，隔离了 1 件 NG」 期望 `{"station": "OP30", "alarm_code": "T-101", "actions_done": ["inspect", "isolate_ng_part"], "ng_count": 1}` 实际 `{"station": "OP30", "alarm_code": "T-101", "ng_count": 1, "actions_done": ["isolate_ng_part"]}`
- [deepseek-v4-flash] `REP-015` 汇报要素抽取：输入「OP30 螺纹孔里有铁屑，角度超差，隔离了一件」 期望 `{"station": "OP30", "alarm_code": "T-102", "actions_done": ["inspect", "isolate_ng_part"], "ng_count": 1}` 实际 `{"station": "OP30", "alarm_code": "T-102", "ng_count": 1, "actions_done": []}`
- [deepseek-v4-flash] `REP-027` 汇报要素抽取：输入「OP30 T-102 角度超差，第三颗螺栓歪了，隔离了 1 件，复位了」 期望 `{"station": "OP30", "alarm_code": "T-102", "actions_done": ["inspect", "isolate_ng_part", "reset_alarm"], "ng_count": 1}` 实际 `{"station": "OP30", "alarm_code": "T-102", "ng_count": 1, "actions_done": ["isolate_ng_part", "reset_alarm"]}`
- [deepseek-v4-flash] `REP-030` 汇报要素抽取：输入「OP30 报警 T-103，扫码枪线缆好像松了，我没插」 期望 `{"station": "OP30", "alarm_code": "T-103", "actions_done": ["inspect"], "ng_count": null}` 实际 `{"station": "OP30", "alarm_code": "T-103", "actions_done": [], "ng_count": null}`
- [deepseek-v4-flash] `SCR-010` 剧本生成：输入「T-106 防错型号不符：要么是上料区混进了别的型号的件，要么是换型时 HMI 上型号没确认，不管哪种，一律停机隔离工件，叫班组长。」 期望 `"通过格式、引用校验与引擎回放"` 实际 `{"ok": false, "summary": "引用校验未通过（2 处）", "format_errors": [], "reference_errors": ["根因 RC-T106-A：初始状态引用了不存在的部件 hmi_varia`
- [qwen3.8-max] `COL-003` 口语与转写变体：输入「刀头是不是坏了」 期望 `[{"action": "inspect", "params": {"target": "bit"}}]` 实际 `[]`
- [qwen3.8-max] `HINT-014` 提示合规：输入「」 期望 `"不泄露根因"` 实际 `"去光栅前面那块地方瞅瞅，看看有没有啥不该搁那儿的东西挡着。"`
- [qwen3.8-max] `REP-014` 汇报要素抽取：输入「OP30 程序号不对，扭矩低，隔离了 1 件 NG」 期望 `{"station": "OP30", "alarm_code": "T-101", "actions_done": ["inspect", "isolate_ng_part"], "ng_count": 1}` 实际 `{"station": "OP30", "alarm_code": "T-101", "ng_count": "1", "actions_done": ["isolate_ng_part"]}`
- [qwen3.8-max] `REP-015` 汇报要素抽取：输入「OP30 螺纹孔里有铁屑，角度超差，隔离了一件」 期望 `{"station": "OP30", "alarm_code": "T-102", "actions_done": ["inspect", "isolate_ng_part"], "ng_count": 1}` 实际 `{"station": "OP30", "alarm_code": "T-102", "ng_count": "1", "actions_done": ["isolate_ng_part"]}`
- [qwen3.8-max] `REP-027` 汇报要素抽取：输入「OP30 T-102 角度超差，第三颗螺栓歪了，隔离了 1 件，复位了」 期望 `{"station": "OP30", "alarm_code": "T-102", "actions_done": ["inspect", "isolate_ng_part", "reset_alarm"], "ng_count": 1}` 实际 `{"station": "OP30", "alarm_code": "T-102", "ng_count": "1", "actions_done": ["isolate_ng_part", "reset_alarm"]}`
- [qwen3.8-max] `SCR-005` 剧本生成：输入「T-107 气源压力低：先看工位手阀是不是只开了一半，开到底就好；手阀全开了压力表还在红区，就是车间供气的问题，叫维修，调压阀不归你调。」 期望 `"通过格式、引用校验与引擎回放"` 实际 `{"ok": false, "summary": "格式错误", "format_errors": ["剧本必须是一个 YAML 映射"], "reference_errors": [], "replay_failed": []}`
- [qwen3.8-max] `SCR-010` 剧本生成：输入「T-106 防错型号不符：要么是上料区混进了别的型号的件，要么是换型时 HMI 上型号没确认，不管哪种，一律停机隔离工件，叫班组长。」 期望 `"通过格式、引用校验与引擎回放"` 实际 `{"ok": false, "summary": "格式校验未通过（2 处）", "format_errors": ["root_causes.0：Value error, RC-T106-A：不在操作工权限内的根因必须写 andon_ta`
- [deepseek-v4-pro] `COL-003` 口语与转写变体：输入「刀头是不是坏了」 期望 `[{"action": "inspect", "params": {"target": "bit"}}]` 实际 `[]`
- [deepseek-v4-pro] `COL-006` 口语与转写变体：输入「屏上报的啥」 期望 `[{"action": "inspect", "params": {"target": "hmi"}}]` 实际 `[]`
- [deepseek-v4-pro] `COL-015` 口语与转写变体：输入「把那个空盒子挪开」 期望 `[{"action": "remove_obstruction", "params": {}}]` 实际 `[]`
- [deepseek-v4-pro] `COL-020` 口语与转写变体：输入「开线」 期望 `[{"action": "resume", "params": {}}]` 实际 `[{"action": "run_production", "params": {}}]`
- [deepseek-v4-pro] `COL-028` 口语与转写变体：输入「看看皮头」 期望 `[{"action": "inspect", "params": {"target": "bit"}}]` 实际 `[]`
- [deepseek-v4-pro] `COL-029` 口语与转写变体：输入「换批投」 期望 `[{"action": "replace_bit", "params": {}}]` 实际 `[]`
- [deepseek-v4-pro] `FAITH-008` 描述忠实度：输入「看看扫码窗口」 期望 `"扫码枪窗口上蒙着一层油污和粉尘。"` 实际 `"扫码枪窗口上蒙着一层油污和粉尘，看起来不太干净。"`
- [deepseek-v4-pro] `INT-050` 意图解析：输入「弄一下」 期望 `"反问澄清"` 实际 `null`
- [deepseek-v4-pro] `REP-014` 汇报要素抽取：输入「OP30 程序号不对，扭矩低，隔离了 1 件 NG」 期望 `{"station": "OP30", "alarm_code": "T-101", "actions_done": ["inspect", "isolate_ng_part"], "ng_count": 1}` 实际 `{"station": "OP30", "alarm_code": "T-101", "ng_count": 1, "actions_done": ["isolate_ng_part"]}`
- [deepseek-v4-pro] `REP-015` 汇报要素抽取：输入「OP30 螺纹孔里有铁屑，角度超差，隔离了一件」 期望 `{"station": "OP30", "alarm_code": "T-102", "actions_done": ["inspect", "isolate_ng_part"], "ng_count": 1}` 实际 `{"station": "OP30", "alarm_code": "T-102", "ng_count": 1, "actions_done": ["isolate_ng_part"]}`
- [deepseek-v4-pro] `REP-027` 汇报要素抽取：输入「OP30 T-102 角度超差，第三颗螺栓歪了，隔离了 1 件，复位了」 期望 `{"station": "OP30", "alarm_code": "T-102", "actions_done": ["inspect", "isolate_ng_part", "reset_alarm"], "ng_count": 1}` 实际 `{"station": "OP30", "alarm_code": "T-102", "ng_count": 1, "actions_done": ["isolate_ng_part", "reset_alarm"]}`
- [deepseek-v4-pro] `REP-028` 汇报要素抽取：输入「OP30，T-110，我检查了一圈，枪身发烫，没复位」 期望 `{"station": "OP30", "alarm_code": "T-110", "actions_done": ["safety_check"], "ng_count": null}` 实际 `{"station": "OP30", "alarm_code": "T-110", "actions_done": ["inspect", "safety_check"], "ng_count": null}`
- [deepseek-v4-pro] `REP-030` 汇报要素抽取：输入「OP30 报警 T-103，扫码枪线缆好像松了，我没插」 期望 `{"station": "OP30", "alarm_code": "T-103", "actions_done": ["inspect"], "ng_count": null}` 实际 `{"station": "OP30", "alarm_code": "T-103", "actions_done": [], "ng_count": null}`
- [deepseek-v4-pro] `SCR-007` 剧本生成：输入「T-109 螺栓缺料：料盒见底了就叫物料员补料；料盒明明是满的还报缺料，那是料位传感器误报，叫维修，别自己调传感器。」 期望 `"通过格式、引用校验与引擎回放"` 实际 `{"ok": false, "summary": "引擎回放未通过：1/2 条正确路径无法通关", "format_errors": [], "reference_errors": [], "replay_failed": ["RC-T10`
- [deepseek-v4-pro] `SCR-010` 剧本生成：输入「T-106 防错型号不符：要么是上料区混进了别的型号的件，要么是换型时 HMI 上型号没确认，不管哪种，一律停机隔离工件，叫班组长。」 期望 `"通过格式、引用校验与引擎回放"` 实际 `{"ok": false, "summary": "引用校验未通过（2 处）", "format_errors": [], "reference_errors": ["根因 RC-T106-B：初始状态引用了不存在的部件 hmi_varia`

## 说明与局限

- rule-baseline 是系统的规则降级模式（模型不可用时的兜底），不是大模型；它的响应时间是本机规则计算耗时
- 单局成本估算口径：主持人 10 次 + 师傅 2 次 + 电话 2 次 + 考官 1 次调用 × 各角色实测平均单次成本（按 AIHubMix 公开价格）
