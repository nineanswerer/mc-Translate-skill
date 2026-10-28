# 可配置语言资源盘点

`scripts/pack_inspect.py` 需要 Python 3.11+，只读 Java 实例，报告写在实例之外且不覆盖既有文件。默认 `en_us → zh_cn` 兼容旧版，也可以配置其他源和目标 locale：

```text
python scripts/pack_inspect.py INSTANCE --output WORK/inventory.json --source-locale de_de --target-locale zh_tw --locks WORK/locks.json
```

`--locks` 可省略，内容为 `{"locked_keys":["namespace:language.key","raw.language.key"]}`。已知 Create 功能字符表在出现时默认锁定，这不是要求其他包安装 Create 或使用本案例的字母表。

脚本扫描顶层源 JAR 及 options 中按低到高排列的已选 file 资源包，只读所选两个 locale 的 JSON。输出明确 source_locale、target_locale，各条目的语言字段使用相应代码；默认调用仍保留 en_us/zh_cn 字段。已有目标译文保留，缺失项仅标候选，不自动发给翻译 API。动态资源、同层冲突和 required/fixed 规则需另查；严格重复键/非法 JSON 会列 errors，不是静默当成没文本。

资源包、Default Options 等被检测出只表示静态线索，运行是否生效仍须日志/用户验收。工具不解析 SNBT、书籍、脚本、旧版 .lang 或图片，不能称全包覆盖。Java/加载器和版本发现用 `project_profile.py`，适配范围见 [project-adapters.md](project-adapters.md)。

测试使用合成 JAR、文件包、不同 locale 和元数据，不读取真实密钥、不联网、不操作游戏。
