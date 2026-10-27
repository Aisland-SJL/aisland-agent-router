# Aisland Agent Router

A Codex skill for main-led delegation, scoped token observations and concise task recaps.

Version: **0.1.0** · [GitHub](https://github.com/Aisland-SJL/aisland-agent-router) · [Release Notes](CHANGELOG.md)

主会话负责决策和最终验收，子 Agent 接手值得交接的探索、执行、专家咨询或独立审查。每次实际启用后，给一份框内文字复盘。

## 使用

- “用小队模式完成这个任务。”
- “检查小队模式”或“检查子 Agent 工作状态”。
- “执行者改成 Luna high。”
- “关闭推理不足触发。”
- “复盘小队。”

也可显式使用 `$aisland-agent-router`。检查使用纵向四角色列表并询问修改，不派发。两类被动触发分别评估简单执行交接和关键推理需求；实际发现取决于宿主。

实际启动协作时会明确提示：`🧭 已触发 aisland-agent-router：<本次协作目的>`。交付复盘框的第一行统一为 `🧭 aisland-agent-router｜交付复盘`，并说明这是 Skill 协作过程的总结；标题保留在框内，单独阅读或复制时也能辨认。检查配置、维护 Skill 或讨论本身不冒充协作启动。

短小、低风险、容易验收的一次交接走轻量路径：提示、派发、主会话验收、简短复盘。不默认创建运行 JSON、查询会话身份或统计、另写报告和索引；项目本身要求的状态更新仍遵守。对照实验、统计/审计或需要持久追踪的任务才使用完整记录流程。用户未要求协作的简单任务由主会话直接处理。

## 发布预设

1. 决策者：主会话当前模型与强度；Astra / high 按需专家支援。
2. 探索者：Luna / medium，请求 Fast，默认只读。
3. 执行者：Luna / xhigh。
4. 检查者：GPT-6 Sol / high，按风险或用户要求启用。

默认最多两名子 Agent 并发、三次生产性尝试。配置 `decision_mode=main`、`review_policy=risk_based`；已有本地选择优先。旧记录沿用历史规则，新任务明确记录是否需要独立审查及理由。

## 本地助手

Python 3.10+，仅标准库：

```text
python -B scripts/router.py config show
python -B scripts/router.py config set --role executor --effort xhigh
python -B scripts/router.py config set --setting review_policy --value risk_based
python -B scripts/router.py record validate --file <run.json>
python -B scripts/usage.py --record <run.json> --update
python -B scripts/router.py record show --file <run.json>
python -B scripts/router.py record summary --files <run-a.json> <run-b.json>
```

默认值在 `assets/default-config.json`，偏好在运行入口 `.local/config.json`，更改增加 revision。`.local/` 不进入 Git，复制安装更新时保留。用量采集仅读取记录内已知会话的 JSONL 统计字段；核对 id 后按任务时间取差值，累计与快照不完整均标注。模型上下文记录不等于后台模型证明；不估算账单或节省。

完整记录的完成检查要求主会话及实际验收证据；需要 Reviewer 时还检查其工作与证据。轻量任务的验收直接留在对话中。文字复盘可放进一个写作块或代码框，完整记录字段与采集边界见 [记录说明](references/records.md)。

## Explorer Fast

[角色模板](assets/agents/aisland_explorer.toml) 请求 Luna medium Fast。用户确认后部署到 `~/.codex/agents/aisland_explorer.toml`，先核对目标文件。宿主须支持具名角色选择或速度参数；安装 Skill 本身不会自动加载该角色。

模板、安装副本和个人入口的固定模型及强度须与本地偏好一致。配置已保存、请求已发出与 Fast 已服务分开验证。接口缺失时如实标注，保留其他角色正常调度。详见 [协作说明](references/workflow.md#explorer-的-fast-请求)。

## 安装与维护

本目录是完整 Skill 包，也是公开仓库根目录。符号链接或复制到 `~/.codex/skills/aisland-agent-router`；复制更新同步公开文件，保留安装入口 `.local/`。个人规划、实际任务记录和本地授权不属于发布内容。

为其他用户部署须由其选择模型预设，发布默认值不替代授权。历史记录和本地偏好不会因更新包而自动改写。

## 验证

准备授权的隔离目录并运行：

```powershell
$env:PYTHONUTF8 = "1"
$env:ROUTER_TEST_TMP = "<authorized-scratch-directory>"
python -B -m unittest discover -s tests -v
```

测试覆盖配置、验收、历史兼容、用量边界和复盘；实时 Fast 仍需宿主运行证据。

## License

[MIT](LICENSE) · Copyright (c) 2026 Aisland.

## 文件

| 路径 | 用途 |
| --- | --- |
| [VERSION](VERSION)、[CHANGELOG.md](CHANGELOG.md) | 发布版本与变更说明 |
| [LICENSE](LICENSE) | MIT 许可证 |
| [SKILL.md](SKILL.md) | 发现与核心流程 |
| [agents/openai.yaml](agents/openai.yaml) | 展示与隐式调用元数据 |
| [assets/default-config.json](assets/default-config.json) | 发布预设 |
| [assets/agents/aisland_explorer.toml](assets/agents/aisland_explorer.toml) | 可选具名 Fast 模板 |
| [references/workflow.md](references/workflow.md) | 派发、审查与恢复 |
| [references/records.md](references/records.md) | 配置、记录与统计口径 |
| [scripts/router.py](scripts/router.py) | 配置、校验与文字渲染 |
| [scripts/usage.py](scripts/usage.py) | 本任务日志统计采集 |
| `tests/test_router.py`、`tests/test_usage.py` | 离线回归 |
