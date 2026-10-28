# Java 整合包语言资源只读盘点

`pack_inspect.py` 需要 Python 3.11 或更高版本，只读取 Minecraft Java 实例，结果必须写到实例目录之外，且不会覆盖已有报告。它不调用网络或付费 API，不导出存档、日志或账号。报告包含被检出的原文和既有译文，应按项目资料妥善保管，不应直接发布未经隐私检查的报告。

```text
python path/to/pack_inspect.py "INSTANCE_DIRECTORY" --output "WORK_DIRECTORY/inventory.json" --locks "WORK_DIRECTORY/locks.json"
```

`--locks` 可省略。附加锁定文件格式如下，键可以写成 `命名空间:语言键` 或未带命名空间的语言键。脚本已内置锁定 `create:create.flap_display.cycles.alphabet`，并保护值中的空格、ASCII 分号及其顺序。该字段是功能数据，并不是要求所有 `alphabet` 键一律如此。

```json
{"locked_keys": ["example:example.functional.delimiter", "example.other.locked.key"]}
```

会盘点顶层 `mods/*.jar`，不把 `mods/.connector` 等生成 JAR 当源模组；识别这些缓存及存档/日志等敏感目录的文件数量和字节数，不读取其内容。按照 `options.txt` 中 `resourcePacks` 的顺序解析选中 `file/` 目录及 ZIP，记录模组与选中资源包中 `assets/<namespace>/lang/en_us.json` 和 `zh_cn.json` 的文件级 SHA-256、来源、层级和候选值。检测 `config/defaultoptions-common.toml` 中 `defaultResourcePacks` 是否遗漏已选文件包，并记录 journal 是否存在。

`candidate_only` 表示按可见静态资源推导的候选值，不代表真正运行时胜出；`ambiguous_same_tier` 表示两个源模组在同一命名空间和语言键提供不同值，不能猜顺序。动态资源、模组提供的资源包、required/fixed priority、全局资源和未扫描目录均须另查日志或验收。已有 zh_cn 文本一律标记保留，英文或非中文的 zh 值标记需人工/上下文复核；此标记不证明译文正确。翻译候选只表示当前扫描来源中没有 zh_cn，仍需建立上下文、术语表、占位符检查和预算。

`errors` 必须人工处理；命令退出码 0 只表示报告已写入，不表示所有内容都已解析或游戏验收。JSON 仅识别 UTF-8 严格格式与字符串值，超过 16 MiB、非法 JSON 或符号链接会记录错误或跳过。KubeJS、SNBT、Patchouli、GUI、硬编码文本和其他语言格式要通过独立适配器处理，不要据此宣称全包覆盖。

测试时先切换到可写的临时工作目录，再执行：

```text
python "path/to/test_pack_inspect.py" -v
```

测试创建并删除随机命名的独立夹具，覆盖低到高优先级、同层冲突、功能键锁定、保留已有中文、动态资源不确定、非法 JSON、源文件未改变和输出目录防护；不触碰真实实例。
