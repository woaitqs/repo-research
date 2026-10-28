[English](README.md) · **中文**

# minipstack：open-pstack 架构的最小复现

这是对 [`ericlitman/open-pstack`](https://github.com/ericlitman/open-pstack)（研究基于提交 `b4c5e3d`，Open Pstack 1.16.0）架构的复现，
约 1,360 行（含文档字符串，不含演示），**只用 Python 标准库**：

> 技能包自己没有 agent 循环。父 harness 读到一段简短的指令、一份技能名列表，以及按需加载的技能正文。
> 当某个角色需要另一家厂商的模型时，由**父会话**一次性决定路由：自己的提供方走原生，其他提供方都通过一个
> **runner**：它只调用一次模型，证明是哪个模型回答的，并且总会留下一份回执。失败的 lane 是一个有名字的
> **掉队（dropout）**，绝不是降级。

它复现的是架构，不是技能内容。测试和演示中的提供方 CLI 都是脚本化的假实现，因此每条论断都能被精确检查。
同一个 runner 也能驱动真实的 `claude` 和 `codex` CLI（见 `real_model/`）。

研究文章：[`research/open-pstack.zh.md`](../../research/open-pstack.zh.md)

## 对应关系

| minipstack | 上游 open-pstack（`b4c5e3d`） | 复现的思路 |
|---|---|---|
| `registry.py` | `runner/registry/models.ts`、`family.ts`、`index.ts` | 唯一的带类型数据源；解析顺序：当前 → 保留的前代 → 滚动别名的修订版 → 未注册；`resolve_lane` 拒绝同提供方 lane、未规范化的固定版本、未列出的 effort 和不支持的访问类型 |
| `adapters.py` | `runner/providers/{claude,codex,grok,devin}.ts`、`shared.ts` | 各访问模式的 argv、预检分类、输出解析、模型证明（提供方报告或固定 argv）、环境策略（继承或白名单）；不含任何策略字段 |
| `runner.py` | `runner/run.ts`、`cli.ts`、`types.ts` | 0600 独占预留；身份清理；在子进程的 `PATH` 上找可执行文件；Claude 写入 lane 禁写共享 `.git`；只在 `unauthenticated` 之后重试一次预检；只调用一次模型；一个绝对截止时间；会通知子进程的取消；状态优先级与退出码 |
| `render.py` | `runner/registry/render.ts`、`registry.test.ts` | 生成的模型目录和原生 agent 文件；指明写入命令的漂移检查；对手写 Markdown 中描述符和带版本号模型名的文本扫描 |
| `dispatch.py` | `references/provider-dispatch.md`、`skills/{interrogate,arena}/SKILL.md` | 父会话决定的路由；子 agent 的任务分配里没有任何路由输入；所有 lane 同时启动；所有 lane 结束前不做评判；只计入有证明的 lane；掉队被记录、绝不替换；一致 = 被 2 个以上模型提出的同一行 |
| `sheet.py` | `skills/setup-pstack/SKILL.md`、`references/codex-tools.md` | 模型配置表；在内存中规范化带版本号的 Claude 固定版本；拒绝只读提供方上的写入角色；写入前探测每个家族；快照、读回、恢复；重复运行字节一致；只有一行导入 |
| `orch.py` | `scripts/orch/store.ts`、`playbooks/orchestrate.md` | 存储位于会话之外；原子写；接管死进程持有的锁；按 PR 和 head SHA 记录的账本；收件箱批量处理；状态从表格派生 |
| `skills.py` | `hooks/`、技能树、`poteto-mode/SKILL.md` | 指令只在 startup/clear/compact 时注入；启动时只有名字和描述；调用时才加载正文；原则叶子隐藏，并按路径解析 |

刻意**没有**复现的部分：技能和 playbook 的文字、Codex 特有的分发（`spawn_agent`）、提供方旁路目录和专属配置
（Devin、Cursor、agy）、Grok 预检输出的确切格式、`watch-pr`、`check-plan.mjs`、`orch` 中的 frontier 计算、
hook 的 Windows 包装器，以及线上门禁。

## 运行

```bash
./run.sh          # venv -> pip install -e .[test] -> 构建 wheel -> pytest -> 演示
./run.sh demo     # 只跑演示，不安装（python3 >= 3.10，PATH 上有 git）
python3 mutation_check.py   # 注入 21 种架构回归；每一种都必须让某个测试失败
```

## 上游探针（需要 `b4c5e3d` 的 open-pstack 克隆，以及 bun 和 git）

```bash
./run.sh probe /path/to/open-pstack
```

`upstream_probe/probe_runner.py` 用脚本化的 `claude`、`codex`、`grok` 和 `devin` CLI（`upstream_probe/fake_cli.py`）
驱动随插件发布的 `pstack-runner` 启动器。它不需要网络，也不需要登录。输出在 `upstream_probe/probe_output.json` 中：
**20 条论断全部成立。** 每条探针的论断和结果见研究文章的[验证](../../research/open-pstack.zh.md#验证verification)一节。

## 真实 CLI（`real_model/`）

这些脚本使用操作者已有的 `claude` 和 `codex` 登录。它们从不读取凭据文件，发布的结果中本地路径都被替换成占位符。

```bash
python3 real_model/run_real_runner.py /path/to/open-pstack --runs 3    # results_run{1,2,3}.json
python3 real_model/run_plugin_session.py /path/to/open-pstack          # plugin_sessions.json
python3 real_model/run_explicit_session.py /path/to/open-pstack        # explicit_sessions.json
PYTHONPATH=src python3 real_model/run_mini_real.py --runs 3            # mini_results.json
```

- `run_real_runner.py` 用真实 CLI 驱动**上游**的 `pstack-runner`：Haiku、`sonnet` 别名和 Codex Luna 的模型证明；
  一次真实的掉队（这个账号的 Codex 不提供 `gpt-6.1-sol`）；以及每种访问模式能拦住什么（R1–R9，3 次）。
- `run_plugin_session.py` 把固定版本的插件加载进无界面的 Claude Code（`--plugin-dir`、`--setting-sources project`），
  测量 SessionStart 指令能否把任务路由进某个 pstack 技能：3 个模型 × 3 个提示 × 3 次。
- `run_explicit_session.py` 显式调用 `/pstack:poteto-mode`，并记录随后发生的事：读 playbook、建待办列表、
  读分发规则、委托（3 个 Sonnet + 3 个 Opus 会话）。
- `run_mini_real.py` 用同样的 CLI 驱动 **minipstack**：每次运行两个 `interrogate` 评审组合（每个父 harness 一个），
  审查一个埋了缺陷的 diff，其中包含两次真实的掉队。

具体数字见研究文章的[验证](../../research/open-pstack.zh.md#验证verification)一节。
