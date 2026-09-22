# DeepSeek Harness 桌面版

[English](README.md) | 中文

DeepSeek Harness 桌面版（`dsh-desktop`）是开源 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) 智能体的 Windows 桌面构建，**开箱即用**（即开即用）：打包的 EXE 与双语安装程序内置了完整后端——`dsh` CLI、`dsh web` 网页界面和 Node.js 运行时——无需先安装 Node.js 或 Python，任何人都可以下载、安装并直接开始使用，对新手（小白）非常友好。

本项目**后端逻辑是对 `dsh` 原仓库的全量拷贝**：完整保留官方 `deepseek-harness` 源码，桌面端外壳、安装程序及其构建脚本位于其上层的 `python/desktop/` 目录。官方更新会持续合并进来，桌面版始终与上游项目保持同步，不断获得官方修复与新功能。

## 快速开始（Windows）

1. 从 [Releases](https://github.com/deepseek-ai/deepseek-harness/releases) 页面下载最新的安装包 `DeepSeekHarness-*-Setup.exe`。
2. 运行并跟随双语（中文 / English）安装向导操作，可选择为当前用户或全体用户安装。
3. 从开始菜单或桌面快捷方式启动 **DeepSeek Harness**。应用会自行启动内置后端，无需 Node.js 或 Python。
4. 点击窗口的 **X** 会将其驻留在系统托盘继续后台运行；右键托盘图标选择 **退出 / Exit** 才会彻底退出。

## 功能特性

- **自带运行时**——内置 `node.exe`、完整的 `dsh` CLI 闭环与网页界面，无需另行安装。
- **Windows 原生窗口**——界面运行在 **WebView2**（Edge 内核，Windows 11 自带 / 随 Edge 提供）。
- **双语安装程序**——中文 / English，智能识别更新：覆盖安装保留你的会话与设置。
- **关闭即驻留托盘**——关闭窗口仅最小化到托盘，只有托盘菜单（退出 / Exit）才能彻底退出。
- **会话数据不丢失**——对话保存在 `%LOCALAPPDATA%\DeepSeekHarness`，升级与卸载均不会删除。
- **持续同步**——官方上游更新不断合入本仓库。

## 相比官方多出的桌面层

在未改动的上游拷贝之上，`python/desktop/` 增加了 pywebview/WebView2 窗口宿主、系统托盘图标与打包流水线。

| 目录 / 文件 | 作用 |
| --- | --- |
| `python/desktop/` | 桌面外壳（pywebview + WebView2）、托盘、配置与构建脚本 |
| `python/desktop/inno/` | 生成安装器的双语 Inno Setup 脚本 |
| `apps`、`packages`、`docs` 等 | 官方 `deepseek-harness` 源码的全量未改动拷贝 |
| `scripts/` | 仓库级构建与双语配对校验脚本 |

## 从源码构建

```sh
git clone https://github.com/deepseek-ai/deepseek-harness.git
cd deepseek-harness
pnpm install
pnpm run build
```

随后可分别用 `python/desktop/scripts/build_desktop.ps1`、`python/desktop/scripts/build_installer.ps1` 与 `python/desktop/scripts/verify_installer.ps1` 产出桌面外壳、双击即用的 EXE 与安装器（`pnpm run build` 仅用于先准备 UI 与 CLI 产物）。

## 与上游同步

本仓库在设计上镜像官方项目：上游 `deepseek-ai/deepseek-harness` 的变更会持续合入本仓库，桌面版随之获得全部修复与新功能。`scripts/` 下的双语配对校验门禁保证中英双语文档的一致性。

## 社区与支持

- 通过 [GitHub Discussions](https://github.com/deepseek-ai/deepseek-harness/discussions) 提交反馈或 bug 报告。
- 为你的插件仓库添加 [`dsh-plugin`](https://github.com/topics/dsh-plugin) 话题，便于被发现。
- 关注官方项目动态与路线图：[DeepSeek AI](https://deepseek.com)。

## 参与贡献

参见 [CONTRIBUTING.zh.md](CONTRIBUTING.zh.md)。

## 开发

请先阅读[开发指南](docs/development.zh.md)与[架构文档](docs/architecture.zh.md)。

`pnpm run dev:web` 会在一个终端里完成构建、启动，并在源码修改时重建 client bundle；`make help` 列出 Web 与 Desktop 对应的 Make target。完整表格见开发指南的「应用命令」一节。

面向 agent：请遵循 [AGENTS.md](AGENTS.md)。

## 引用

```bibtex
@misc{deepseek-harness2026,
  title={DeepSeek Harness: Everything is a Plugin},
  author={DeepSeek-AI},
  year={2026},
  publisher={GitHub},
  howpublished={\url{https://github.com/deepseek-ai/deepseek-harness}},
}
```


## 许可证

[MIT](LICENSE)

第三方依赖及其许可证见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。