# 增量批量翻译编排

`scripts/batch_translate.py` 调用同目录的 `translate_driver.py`、共享 SQLite 账本和文本保护器；仅组织已明确挑选的显示文字，不扫描、写入、部署或启动游戏。它需要 Python 3.11+，且在真实发送前必须完成 [外部服务与预算核对](translation.md)。HTTP 示例配置默认拒绝发送，任何联网调用须符合用户授权与环境规则。

参考 `assets/batch-plan.example.json` 准备计划。每行项必须有唯一 `id`、准确 `key`、`kind`、`source`，只有 `status=pending_translation` 才可进入翻译队列；补充真实上下文，标 `already_correct`、`existing_zh` 或 `locked` 会保留而不外发。功能字段、技术类型、纯问号、含中文的混合文本和缺少上下文所需字段按安全规则跳过或交人工复核，不自动覆盖已有译文。需要审核已有中文时先在独立流程判定是否需调整，再显式准备新任务；不能仅删标记就批量重译。

同原文、完整相同上下文和类型的条目可复用；缺上下文时按键隔离。请求基座必须包含模型、`prompt_version` 与 `glossary_version`；按需加入术语和场景。修改提示、术语、模型、服务/计费配置必须升级这些版本或适配器 identity；不同义项不可共用上下文。输出即使通过格式检查也只表示 `translated_structural_check_only`，仍要语义审核和游戏验收。可疑、未知或未处理项目显示 `needs_review`，预算/请求问题为 `blocked`。

```text
python scripts/batch_translate.py \
  --plan work/batch-plan.json --adapter work/adapter.json --db work/ledger.sqlite \
  --state work/batch-state.json --output work/batch-results.json \
  --max-cost 单批保守预留 --batch-size 8 --workers 2 \
  --pause-file work/pause.marker
```

路径/单批上界须替换为实际值；在 Windows PowerShell 中请按其换行规则运行，或写成一行。将状态、结果和账本放独立的任务工作目录，不含实例、密钥或公开输出。先检查该任务唯一 ledger 的累计上限和余额，`--max-cost` 必须覆盖每个批次最坏费用；它不是总上限。默认 1 worker，最多 8；先小样后再根据真实限流设置。单协调器通过 state 锁避免同时改写同一批次计划，多任务/多代理必须共用该任务同一账本。

程序将计划内容、目标、服务身份、预算参数和账本路径的指纹写入状态。中断后用完全相同的计划、适配器 identity、账本、输出、批量大小、预留和暂停文件路径重跑；已确认的翻译从账本缓存恢复，结果不明和付费失败不会自动重发。计划变更会拒绝旧状态，需由协调者审计影响并建立新计划；历史账本不重置。结果文件在再次运行前及写入前核对上次哈希，用户后来编辑或未知文件将保留并停下。若进程恰在结果写入后、状态哈希记账前中断，也会安全停止；先保留结果、核对计划与 ledger 并手动记录恢复证据，不要直接覆盖或重跑付费请求。

用户要求暂停时，协调者创建约定的暂停标记文件；开始前已存在标记则不发送请求，运行中检查到标记后停止派发新批次，已经提交/正在执行的请求可能仍产生费用并计入同一账本，不杀进程。检查标记与发送之间仍有并发竞态，不能保证瞬时零新请求；用低并发缩小窗口。用户明确恢复后删除该标记并使用原参数续跑，不能把超时当成恢复授权。

仅明确 `not_sent` 的请求可按 `--max-not-sent-retries`（0–4）及 `--backoff`（0–5 秒，指数退避且上限 5 秒）有限重试；默认重试一次。`uncertain` / `already_in_flight` 必须核对账单或回收响应，`paid_output_needs_review` 先用恢复工具离线检查，确实不可恢复再按账本流程记录重试理由并重新预留；`budget_exhausted` 和 `budget_frozen` 停止派发。已有并发请求独立结算，不能保证供应商违反承诺时总额绝不超限。

运行 `python scripts/test_batch_translate.py -v` 可用假适配器验证：上下文去重、正确中文/功能数据保护、预算耗尽、未知请求不重复、已付费失败冻结、暂停与恢复、输出篡改保护、整批发送前凭据检查。测试不联网、不读取真实密钥、不产生费用；不证明实际供应商协议或语义品质。
