# Minecraft 整合包汉化 Skill

面向 Minecraft Java 整合包汉化的可复用工作流、参考资料和 Python 工具。目标是先复用可靠中文，再用用户认可的外部服务批量翻译，并通过预算、缓存、格式保护、反馈修复和用户验收组织交付。

## 使用

将本仓库克隆到支持本地 Skill 的 AI 工具所使用的技能目录，并将目录命名为 `minecraft-modpack-localization`。Codex 的个人技能目录通常为 `~/.codex/skills/`：

```sh
git clone https://github.com/nineanswerer/mc-Translate-skill.git ~/.codex/skills/minecraft-modpack-localization
```

在 Codex 中调用：

> 使用 `$minecraft-modpack-localization` 汉化指定整合包。优先复用现有中文，核对外部服务和累计预算后批量翻译，最终制作目标启动器可安装的整合包，并组织我亲自验收。

其他能读取文件、执行脚本的 AI 可直接读取根目录 [SKILL.md](SKILL.md)，再按其中引用逐步加载资料。技能发现、监视、子智能体及权限机制需适配目标平台；纯聊天工具无法仅凭这些文件独立操作电脑。

## 内容

- [SKILL.md](SKILL.md)：执行入口、阶段和授权边界。
- [完整案例复盘](references/case-study.md)：Desecrated 汉化中的真实失败、修复及用户决策。
- [工具与配置](references/tooling.md)、[批量续跑](references/batch-cli.md)：预算、缓存、外部适配器、暂停及恢复。
- [格式保护](references/formats.md)、[交付与回滚](references/delivery.md)、[用户验收](references/acceptance.md)。
- `scripts/`：可执行工具与离线测试；`assets/`：不含凭据的配置模板。

## 环境与验证

需要 Python 3.11+，运行工具只使用标准库。真实翻译服务需用户自行授权，并按当时官方接口、模型、地域、价格、usage 和可靠费用上界配置。模板默认不发送付费请求；密钥仅在本地受限环境中配置，不要提交到仓库。

离线测试：

```sh
python -B -m unittest discover -s scripts -p "test_*.py" -v
```

本版包含 39 项离线测试。测试使用本地假响应、假模组和独立夹具，不访问真实翻译服务、不启动游戏。测试里的假凭据仅用于验证脱敏。

内置盘点主要支持 Minecraft Java 的 en_us/zh_cn 语言 JSON；SNBT、脚本、书籍、硬编码和未知加载器需要按文档使用专用适配器或补充实现。格式检查不能证明翻译语义和游戏画面正确。更多已验证内容及限制见 [验证范围](references/validation.md)。

案例中 PCL 首次启动未自动启用中文资源包的问题已在新导出副本中修正配置，但修复后的全新安装仍待用户实机验收。不要把文件校验、安装器识别、运行时加载和玩家实际验收混为一谈。

## 来源与署名

本次汉化案例的译者署名为「九术一华」。原整合包作者 ReversTeam、社区译文及原生模组译文的来源说明保留于案例资料；该名称是案例参数，不应自动用于其他项目。

本仓库发布工作流、工具、模板和去敏复盘，不包含游戏整合包、模组本体、第三方美术资源、用户存档或 API 密钥。案例中作品与译文的授权范围仍由其原许可证决定。
