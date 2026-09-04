# dsh 桌面外壳(`python/desktop`)

[English](README.md) | 中文

DeepSeek Harness web UI 的轻量 Python 桌面外壳。它拉起本地 `dsh web`
后端,并把后端服务的 web 界面嵌入 **WebView2** 窗口(经
[`pywebview`](https://pywebview.flowrl.com/))。它取代了旧的 PySide6
部件移植版——那个版本用原生 Qt 渲染每个屏幕;本外壳直接承载 `dsh web`
命令所服务的同一套浏览器 UI,因此没有需要双重维护的界面副本。

```
┌────────────────────────────────────────────┐
│ WebView2 window (Edge Chromium, GPU accel) │
│        ┌─────────────────────────┐         │
│        │  dsh web UI (SPA)        │         │
│        │  ──HTTP / WebSocket──┐   │         │
│        └─────────┬────────────┘   │         │
└──────────────────┼─────────────────────────┘
                   │ localhost
         ┌─────────▼──────────┐
         │ node …/bin.js web  │  spawned by BackendSupervisor
         │ --port 0 --no-open │  (CREATE_NO_WINDOW, auto-restart)
         └────────────────────┘
```

Python 侧**只做窗口宿主**:拉起后端、等待打印的 URL、在窗口中打开它。
浏览器经 localhost 直连后端——没有 Python↔JS 桥、Python 里没有
JSON-RPC 客户端、也没有轮询循环。

## 运行(开发)

```powershell
# once: workspace build so apps/cli/lib/bin.js + apps/web/dist exist
pnpm run build

# launch the shell (creates python/desktop/.venv + installs deps on first run)
.\scripts\run_dev.ps1
```

或手动:

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python -m dsh_shell
```

### 覆盖项

| 开关 | 环境变量 | 作用 |
| ---- | -------- | ---- |
| `--node` | `DSH_DESKTOP_NODE` | 后端使用的 Node 可执行文件 |
| `--cli-dir` | `DSH_DESKTOP_CLI_DIR` | 存放 `lib/bin.js` 的目录(默认 `<repo>/apps/cli`) |
| `--data-dir` | `DSH_DESKTOP_DATA_DIR` | 可写数据目录(默认 `%LOCALAPPDATA%\DeepSeekHarness`) |

## 各层职责

| 关注点 | 归属 |
| ------ | ---- |
| UI 渲染、markdown、工具卡片、设置 | WebView2 中的 web UI(`dsh web`) |
| 后端 HTTP/WebSocket API | 被拉起的 `node` 进程([`dsh_shell/backend.py`](dsh_shell/backend.py) 中的 `BackendSupervisor`) |
| 窗口、后端拉起、崩溃监督 | `dsh_shell`(`app.py`) |

后端崩溃可自愈:`BackendSupervisor` 以指数退避重启 `dsh web`,窗口重新
绑定新端口。会话在服务端持久化(sqlite/jsonl),因此刷新即恢复对话,
无需重启外壳。

## 性能要点

1. **零 Python 桥** — 浏览器经 localhost HTTP/WebSocket 直连后端;没有
   每帧 Python 工作,没有 IPC 放大。
2. **`debug=False`** — `webview.start(debug=False)` 走 WebView2 生产渲染
   路径(默认开启硬件加速)。
3. **持久 WebView2 缓存** — `WEBVIEW2_USER_DATA_FOLDER` 指向
   `%LOCALAPPDATA%\DeepSeekHarness\webview2`,跨启动复用 HTTP index /
   资源 / GPU 着色器缓存 → 重复启动明显更快。
4. **仅 localhost** — 无远程 CDN、无混合内容意外;SPA 由本地后端服务。
5. **崩溃自愈且保留会话** — 后端重启原位重绑窗口,而不是重启整个外壳。
6. **`CREATE_NO_WINDOW`** — node 子进程永不闪现控制台窗口。

## 说明 / 限制

- 当前窗口后端仅限 Windows(`pywebview` + WebView2 运行时,后者在
  Windows 11 预装 / 随 Edge 附带)。

## 打包:EXE + 安装器

构建双击即用的 EXE 与双语安装器(zh-CN / en),完全自包含(自带
node.exe + `dsh` CLI 闭包 + `apps/web` dist):

```powershell
# 1) the PyInstaller windowed onedir EXE (boot-probes it standalone)
.\scripts\build_desktop.ps1

# 2) the Inno installer (reads the version from apps/cli/package.json)
#    requires: winget install JRSoftware.InnoSetup
.\scripts\build_installer.ps1

# 3) end-to-end installer verify: full install -> update branch -> uninstall
#    (independent scratch dirs; user data preservation asserted)
.\scripts\verify_installer.ps1
```

流水线(开关见 `scripts/build_desktop.ps1`,如 `-SkipDeploy`):

| 步骤 | 产物 | 内容 |
| ---- | ---- | ---- |
| preflight | — | 校验 `apps/cli/lib/bin.js` + `apps/web/dist`(缺失则 `pnpm run build`) |
| closure deploy | `.build/resources/cli` | `pnpm --filter dsh-desktop-runtime deploy --prod --legacy` → 自包含 `node_modules`;此处应用三个完整性修复:(1) vendor 覆盖被解除 link 的依赖物化为真实副本,(2) 被 `link:` 覆盖的 `@deepseek-ai/{cosmokit,schemastery}` 从 `vendor/` 物化(pnpm 从不在输出树里交付它们),(3) 仅作为 peer 的 `workspace:` 包(`@deepseek-ai/dsh-*`、`cordis-plugin-group`)因 deploy-root 把它们声明为**常规**依赖而得以安装——pnpm 不会在 deploy 中自动安装 `workspace:` 协议的 peer |
| closure smoke | `.build/closure-smoke/` | EXE 前启动门:用 node 以 `<closure>` 启动 `dsh web --port 0 --no-open` 并断言打印 HTTP URL——坏闭包在数秒内使构建失败,不让 PyInstaller 白跑约 5 分钟 |
| runtime resources | `.build/resources/{node.exe, icon.ico}` | 捆绑的 node + 应用图标 |
| PyInstaller | `.build/dist/dsh-desktop/` | 窗口化(`console=False`)onedir:`DeepSeekHarness.exe` + `_internal/` |
| relayout | `.build/dist/dsh-desktop/{node.exe, cli, icon.ico}` | 收集完成后,`Relayout-Resources` 把捆绑的运行时从 `_internal\resources` **移到捆绑根**(EXE 的同级)。PyInstaller 只能把它们放在 `_internal/` 下,但 Inno/ISCC(无 `longPathAware`)与安装器的改名覆盖写入受 MAX_PATH(~260)限制;让 `node.exe`/`cli`/`icon.ico` 与 EXE 同级,即使用户真实安装路径很长,每条已安装路径也都保持很短 |
| boot probe | `.build/probe/`(含 `shell.log`) | 独立启动 EXE:窗口出现,捆绑的 `<app>\node.exe`(捆绑根)拉起 `dsh web`,WM_CLOSE 干净退出,无残留 |

EXE 外壳冻结态下解析一切:`sys.frozen` 让 `config.py` 从**捆绑根**——
`DeepSeekHarness.exe` 所在目录——读取 `node.exe`、`cli` 闭包与
`icon.ico`(仍可用 `DSH_DESKTOP_NODE` / `DSH_DESKTOP_CLI_DIR` 覆盖)。
启动诊断写入 `%LOCALAPPDATA%\DeepSeekHarness\shell.log`(无控制台)。
EXE / 快捷方式 / 任务栏图标来自内嵌 PE 图标(`icon=assets/icon.ico`);
窗口内标题栏图标(pywebview 在 Windows 上没有 `icon=`)由 `app.py` 在
显示后经 `WM_SETICON` 设置(仅 `icon.ico`;开发启动保持通用表单图标)。
命名互斥体(`Global\…`)与安装器的 `AppMutex` 匹配,使第二次启动 /
安装能发现运行中的副本。

Inno 安装器(`inno/setup.iss`)是双语的、跟随系统语言;安装时提供
按用户 / 全用户;并自动检测**全新 vs 更新**:已存在安装(同 `AppId`)
会跳过许可 / 目录 / 快捷方式页,保留安装目录与**全部用户数据(在
`%LOCALAPPDATA%\DeepSeekHarness`,位于 `{app}` 之外)**,并在降级时警告。
卸载只删除 `{app}`——对话与设置得以保留。

### Deploy root

`deploy-root/` 是一个仅含 `package.json` 的 workspace 成员,声明
`"@deepseek-ai/dsh": "workspace:^"` **外加每个 pnpm deploy 否则会静默
丢弃的 `workspace:` 协议 peer/optional 依赖**(`@deepseek-ai/dsh-*`
功能包、`cordis-plugin-group`、`@deepseek-ai/{cosmokit,schemastery}`
——后两者在 deploy 之后仍会缺失,因为根 `overrides:` 把它们映射到
`link:vendor/*`,所以 `build_desktop.ps1` 从 `vendor/` 显式物化它们;
仅限 Linux 的 `node-addon-landlock-run-linux-*` 包刻意不包含)。它
**不是构建目标**——存在意义是让 `pnpm deploy` 产出物理自包含的 CLI
闭包。它注册在根 `pnpm-workspace.yaml`(`python/desktop/deploy-root`)。
