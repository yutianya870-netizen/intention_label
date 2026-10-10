# Intent Lab · LLM 意图标注工作台

本地 HTML 界面 + Python 标准库服务，无须 npm、pip 安装。参考 task1009-main 的会话导航、四类标签和人工标签交换格式；新实现模型调用与实验结果管理。原项目没有被修改。

## 启动

macOS 双击本目录的 `start.command`，或在终端运行：

```sh
cd '/Users/yutianya/Developer/projects/意图/output/intent-labeler'
python3 -B server.py
```

然后打开 **http://127.0.0.1:8789**。Windows 使用 `START.bat`；需要 Python 3.9+。端口占用时 `python3 -B server.py --port 8790`。关闭终端或 Ctrl+C 停止服务。**不要仅双击 index.html**，API 连接需要这个本地服务。

## 四步使用

1. **导入会话**：多选 JSON、JSONL、参考项目的 `sessions.js` 或 `sessions_sample100.js`，也可拖入网页。没有数据时用合成演示试界面。重复导入相同会话自动去重。组别在导入时写入每个会话；自然样本与补充集分开导入、分开比较。
2. **模型与提示词**：填写模型 ID、API Key、Base URL，勾选模型配置与 P1/P2/P3。可复制或添加任意数量模型。同一厂商的不同模型作为不同配置。P2/P3 必须填写同一组真实校准示例；默认没有虚构 few-shot。不要用测试集标签作示例。
3. **开始模型打标**：先选“当前 prompt”确认接口可用，再选当前会话或全部。组合为选中模型 × 选中提示词 × 重复次数。数量上限按当前导入顺序选前 N 条，**不是随机抽样**。顺序调用，暂停在当前请求完成后生效；再运行可跳过相同配置成功项。401/403/429 自动暂停，避免连续失败请求。
4. **核查与导出**：查看依据、待复核项和实际请求体；人工 gold 单独保存，不覆盖模型结果。导出真正 `.xlsx`，另下载完整 JSON 备份用于恢复。恢复备份会合并会话与结果，相同 ID 的人工标签采用备份中的值。

## API 接法

| API 协议 | Base URL 示例 | 自动追加的路径 |
|---|---|---|
| OpenAI Responses | `https://api.openai.com/v1` | `/responses` |
| OpenAI 兼容 Chat | `https://api.deepseek.com/v1`，或你的 Qwen / OpenRouter / vLLM API 前缀 | `/chat/completions` |
| Anthropic Messages | `https://api.anthropic.com/v1` | `/messages` |
| Gemini generateContent | `https://generativelanguage.googleapis.com/v1beta` | `/models/{模型ID}:generateContent` |

模型 ID 以你账户实际可调用的 API 名称为准；网页订阅登录不会自动提供 API 凭证。本地 vLLM 等无鉴权服务可留空密钥。Base URL 含版本前缀，不重复填最终接口路径。

默认不发送 temperature，兼容部分推理模型。额外 JSON 参数可设置 `temperature`、`top_p`、`seed`、`reasoning_effort`、`reasoning`、`thinking`、`enable_thinking`、`max_completion_tokens`、`generationConfig`、`response_format`；是否支持由模型决定。例如兼容 Chat 需要新输出参数时填 `{"max_completion_tokens":4096}`，程序会移除旧 `max_tokens`。Gemini 的温度填 `{"generationConfig":{"temperature":0}}`。不能通过额外参数注入 messages、tools 或会话 ID。

接口实现依据：[OpenAI Responses](https://developers.openai.com/api/docs/guides/migrate-to-responses)、[Anthropic Messages](https://platform.claude.com/docs/en/api/messages/create)、[Gemini generateContent](https://ai.google.dev/api/generate-content)。协议兼容不代表所有模型参数均已实测。此版本通过本地模拟 API 验证，未使用你的付费 API。

## 数据格式与历史边界

支持标准 `messages` / `turns` 的 JSON 对象或数组、JSONL 一行一个会话或消息、Claude Code 原始日志、Codex `response_item` / `event_msg` 日志，以及 JSON 数据赋值格式的 `window.CLEANED_SESSIONS = [...]`。JS 文件作为 JSON 解析，绝不执行。

```json
{"session_id":"conversation-001","messages":[
  {"role":"user","content":"为什么没有搜索结果？"},
  {"role":"assistant","content":"当前只搜索标题。"},
  {"role":"user","content":"搜索标题还是全文更合适？"},
  {"role":"assistant","content":"这条回答不能用于上一条的分类。"}
]}
```

以上第二条用户消息的输入包含第一条 user、第一条 assistant、当前 user，**不包含第二条 assistant**。后端 `context_for()` 先做 `turns[:target_index + 1]`，再序列化；不同厂商共享这个入口。数据历史作为一个 JSON 分析对象传给标注模型，不会作为需要继续执行的编程对话。

只保留公开 user / assistant 文本。工具调用/结果、私有 thinking/reasoning、纯环境注入、文件快照、sidechain 等排除；Codex 优先使用 canonical 消息避免 event 双计。原文件顺序作为时间顺序，不自动按 timestamp 排序。图像不会上传或识别。工具结果中的文本不属于此版“公开会话文本”范围；如果研究需要它，应先修改并固定上下文协议。部分非标准注入仍可能表现为普通 user 文本，须核查导入报告及 prompt 列表。

参考项目里 `data/cleaned/index.json` 只有索引，请导入相邻的 `sessions.js`；100 会话版本可用 `portable_ir02_annotator/data/cleaned/sessions_sample100.js`。不直接读取远程 Hugging Face，需要先下载数据。

## 人工标签兼容

先导入相同会话，再使用“恢复备份 / 导入人工标签”。支持参考网站 `ir02.labels.*` JSON（`labels` 字典）、JSON 行数组及 Excel/CSV。新 Excel 优先读取“人工标签”sheet，其余读第一张表。字段 `task_key` 优先，否则使用 `turn_id` 与可选 `conversation_id` / `session_id` 匹配；多重匹配会跳过并报告。旧 `understand/decide/execute/realign` 映射为 U/D/E/R。**只应把已裁决 gold 文件导入 gold 槽**，不把 A/B 任一人的原始标注当作共识。

## 导出和可复现性

Excel 含模型判定、人工标签、实验配置（提示词全文与哈希）、分类指标、导入报告、使用说明和超长文本记录。每次判定记录源文件、会话/回合 ID、截断位置、历史消息数、实际请求体哈希、模型、提示方案、重复编号、时间、token usage（厂商原始统计）、原始模型输出和错误。

Excel 单元格长文本超 30,000 字符时显式截断；**完整 JSON 备份保留全部会话、提示词和原始输出**。依据保存的会话、目标位置、配置快照和提示词可以重建输入。会话历史不在每条结果里重复存储，避免大型实验冗余。

结果页按组别、模型配置、提示词哈希、重复编号分组；多次重试同一目标取最新记录做指标。Accuracy / Macro-F1 使用四类 gold 中成功解析的预测；模型 OTHER / UNCLEAR 在四类 gold 上计为不正确。另显示将 API 失败计为不正确的端到端 accuracy。没有 gold、类别缺失或 R 极少时，指标仅供诊断，不构成分类可靠性或排名结论。人工 OTHER/UNCLEAR 不进入四类指标。

## 本地保存

API Key 仅在页面内存，刷新需重新填写；不进入 IndexedDB、备份或 Excel。会话/标签/结果保存在本浏览器的 IndexedDB，清理浏览器数据会丢失；备份可转移到另一台电脑。服务仅监听 127.0.0.1，模型请求从本地服务转发到你填入的 API 地址。预览只调用本机，不调用付费 API；开始打标才会发送数据。

## 验证

```sh
python3 -B -m unittest discover -s tests -p 'test_*.py' -v
```

关键验证：未来回答 canary 不出现在四种 API 请求中；前文回答仍保留；gold 不作为输入；解析实际参考数据；模拟接口返回与错误处理；浏览器导入、运行、恢复、指标与 Excel 往返。界面 QA 记录见 `tests/qa-report.json`。

## 第三方组件

`static/vendor/xlsx.full.min.js` 来自你提供的 task1009-main，文件保留 SheetJS 版权头。SheetJS Community Edition 使用 Apache-2.0；许可见 `THIRD_PARTY.md` 与附带 `LICENSE-SheetJS.txt`。除此之外无前端 CDN 或外部依赖。
