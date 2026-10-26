# World Is Agent

**让游戏里的角色拥有身份、记忆，并根据正在发生的事情自主作出回应。**

World Is Agent（WIA）在本机运行，通过游戏 Adapter 接收实时状态、调用大语言模型，再把角色的对话或行动交回游戏执行。模型配置、游戏切换和运行记录都可以在本地 Web 控制台中管理。

![World Is Agent 架构概览](docs/images/world-is-agent.jpg)

> 架构图展示可扩展的模型接入方向；当前版本正式支持 DeepSeek 和 OpenAI。

## 你可以用它做什么

- 让游戏角色结合身份、当前环境和历史记忆进行对话与决策。
- Use the Chinese pixel console to configure games and models, inspect recent turns, and browse saved appointment tasks by world and status.
- 在同一个 Runtime 进程中切换游戏，保留各游戏已经记录的历史。
- 使用 DeepSeek 或 OpenAI，也可以配置兼容接口的 Base URL。

## 支持的游戏

| 游戏 | Adapter | 运行环境 | 安装说明 |
| --- | --- | --- | --- |
| Stardew Valley | 0.1.1 | Windows、SMAPI | [Stardew Valley Adapter](https://github.com/zlci7/world-is-agent-adapters/tree/main/stardew-valley) |
| RimWorld | 0.1.0 | RimWorld 1.6、Windows x64 | [RimWorld Adapter](https://github.com/zlci7/world-is-agent-adapters/tree/main/rimworld) |

Runtime 和游戏 Adapter 分开发布。安装 WIA 时需要一个 Runtime，以及与你所玩游戏匹配的 Adapter。

The development source includes Chinese definitions for all 34 ordinary Stardew friendship NPCs. With a source-built Stardew Adapter 0.1.2, it also provides bounded progress facts, preserves native gift/quest interactions, and exposes an experimental single-player Willy fishing-objective context. Synthetic real-model checks cover dialogue and [bounded DeepSeek tool-argument correction](docs/phase11/tool-call-correction.md); the original unclassified parsing incident, content quality, and live-game acceptance remain open. See the [acceptance record](docs/phase11/phase11.15-acceptance.md). Existing data roots keep their definitions until explicitly updated.

Development builds also record confirmed single-player Stardew gifts as save-scoped experiences. Recent gifts have a separate bounded context block that survives conversation growth, compaction and Runtime restart; expired facts remain available to literal topic search. Receipt membership filters experience sources after rollback. This feature has [background acceptance](docs/phase11/recent-experiences-acceptance.md); in-game gifting and natural dialogue quality remain unverified.

Development source adds optional background knowledge retrieval: a validated Game Profile catalog feeds both automatic pre-turn retrieval and the read-only `search_knowledge` tool. Stardew ships nine topics alongside a smaller Game Definition; core identity and rules stay in every request. Knowledge remains separate from personal memory and current game facts. Existing configurations keep knowledge disabled until explicitly updated. See [knowledge testing and configuration](docs/development/testing.md#knowledge-retrieval) and [acceptance](docs/phase11/phase11.3-acceptance.md); synthetic real-model checks do not establish live-game dialogue quality.

The development source also includes a **hosted invitation beta** for Windows Stardew players: invitation + verified-email/password accounts, emailed password recovery, stable user IDs, personal WIA Keys, one private Runtime/data volume per account, a Chinese account website, explicit preferences and model quotas. A separate administrator website provides scoped login, invitation/user management, personal and global daily token limits, usage audit and bounded CSV export; see [administration](docs/development/hosted-administration.md). Hosted players use SMAPI + Mod + WIA Key; the operator supplies the server, mail sender and model credential. Stardew Adapter 0.1.3 source adds HTTPS connection settings on F8. Local automated and browser validation is available; real mailbox delivery, Linux Docker deployment, public TLS/game connectivity, capacity and full-volume restoration remain unverified. See the [deployment guide](deploy/hosted/README.md), [hosted acceptance](docs/phase11/phase11.2-acceptance.md), and [email/admin acceptance](docs/phase11/phase11.2-email-admin-acceptance.md).

## 使用前准备

- Windows x64。
- 已安装的 Stardew Valley 或 RimWorld。
- 对应游戏的 WIA Adapter。
- DeepSeek 或 OpenAI API Key。

## 开始使用

1. 从 [Releases](https://github.com/zlci7/world-is-agent/releases) 下载 `world-is-agent-v0.2.0-windows-amd64.zip` 并解压。
2. 从 [官方 Adapter 仓库](https://github.com/zlci7/world-is-agent-adapters) 安装对应游戏的 Adapter。
3. 运行 `wia-runtime.exe`，浏览器会自动打开本地控制台。
4. 在页面中选择游戏，填写模型服务商、模型和 API Key。
5. 等待状态变为 **运行服务已就绪**，然后启动游戏并加载存档。

如果 Releases 页面中还没有对应版本，说明该版本尚未正式发布。不要混用不匹配的 Runtime 和 Adapter 版本。

## Runtime 怎样连接游戏

网页不需要填写游戏安装目录。Adapter 安装在游戏的 Mod 目录中，游戏启动后会自动连接本机的 `127.0.0.1:50051`。

建议先启动 Runtime、选择游戏并等待 **Ready**，再启动游戏。连接暂时中断时，当前版本的 Stardew Valley 和 RimWorld Adapter 会自动重试。

In **游戏与模型 (Game & Model)**, applying a game selection cancels the current turn, retains recorded history, and waits for the matching Adapter. Model changes take effect after a successful probe. **预约任务 (Appointments)** shows saved tasks, world readiness, results and cleanup status; the page is read-only and closing it does not stop saved tasks. Stardew enables appointments; RimWorld currently does not. The [testing guide](docs/development/testing.md#appointment-recovery-and-console) records the live-game recovery checks still required.

## 本地数据与 API Key

Runtime 默认把数据保存在：

```text
%LOCALAPPDATA%\WorldIsAgent\
├── config\     游戏与模型配置
├── secrets\    API Key 文件
└── data\       回合 Trace
```

API Key 不会写入普通配置文件，也不会由网页返回。当前版本使用本地文件保存密钥，尚未接入系统密钥链。

## 当前发布状态

Stardew Valley 与 RimWorld 的基础实机闭环已经通过。Runtime 0.2.0、Stardew Adapter 0.1.1 和 RimWorld Adapter 0.1.0 正在完成发布前的运行时切换、自动重连和模型修改验收；对应 tag 与 Release 在验收完成后发布。

## 开发者入口

从源码构建 Windows Runtime：

```powershell
.\scripts\release-runtime.ps1
```

- [系统架构](ARCHITECTURE.md)
- [当前状态](docs/STATUS.md)
- [开发指南](docs/development/guide.md)
- [测试与验收](docs/development/testing.md)
- [Protocol](protocol/README.md)

## 许可证

[MIT License](LICENSE)
