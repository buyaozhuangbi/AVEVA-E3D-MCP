# AVEVA E3D MCP Server

面向 AVEVA Everything3D（E3D）的模型开发与调试 MCP 服务。Python MCP server 通过标准输入/输出（stdio）连接 Codex、Claude Desktop、Cursor 等客户端；需要读取真实 E3D 状态时，再由 E3D 进程内的 .NET Addin 提供本机 HTTP bridge。

当前 MCP catalog 注册 **114 个工具**。`tools/list` 返回的工具定义和可选功能是当前实例能力的权威来源；实际能力受 E3D 版本、加载的 Addin 和配置影响。Python distribution metadata 当前版本为 `0.1.0`（见 `pyproject.toml`）；它不是 E3D Addin build 版本。

## 能力概览

- **会话与上下文**：检查连接、E3D 版本、项目、MDB、当前元素和操作状态；保留会话边界及读取错误。
- **模型读取与诊断**：按显式元素读取层级、属性、Schema、数据库状态、设备和管道证据；对缺失、截断、部分结果和未知状态分别报告。
- **PARAGON 目录工作流**：读取 Catalogue 清单、Schema 和现有 SCOM 元件模板；有限生成计划可为同一 CATE 内复制现有 SCOM 模板形成可审核草稿。
- **几何与图纸**：提供有界几何观察、尺寸与连接点读取，以及参数化几何、剖面、装配、布置图和可选 SVG/DXF/PNG 输出。派生网格或图纸不等同于 E3D 原生几何或工程验收。
- **受控编辑与 PML**：catalog 也包含明确目标的编辑、复制、显示/视图操作和 PML 工具。是否可执行由运行模式、工具参数和权限开关共同控制；请在真实会话里逐项检查目标与回执。

PARAGON 的 Catalogue 数据库由用户在 E3D ADMIN 中配置。本服务不会替用户配置 ADMIN，也不会由生成计划创建数据库、CATE 或元件。该计划只生成候选草稿，不会自动提交 PML 预览或执行。

## 要求

- Python 3.10+ 运行 MCP server。
- `pytest` 用于 Python 测试。
- 要构建 E3D21 Addin，需要 Windows 上兼容的 AVEVA E3D 2.1 安装/SDK 和 .NET Framework 4.8 targeting pack。AVEVA SDK、E3D runtime 和安装文件不随仓库分发。
- 几何布尔相关可选工具需要额外安装 `geometry` 依赖。

## 快速开始：Mock 模式

Mock 模式可在没有 E3D 的情况下启动 MCP server 并发现工具。克隆仓库后，在项目目录运行：

```powershell
git clone https://github.com/buyaozhuangbi/AVEVA-E3D-MCP.git
Set-Location AVEVA-E3D-MCP
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
$env:E3D_MCP_MODE = "mock"
python -m aveva_e3d_mcp
```

服务通过 stdio 运行，通常由 MCP 客户端启动，而不是在终端中手动保持一个进程。若本机没有全局安装本项目，可在 MCP client 配置中将 `PYTHONPATH` 指向仓库的 `src`。环境设置从 MCP server 进程继承；`.env.example` 仅供参考，程序不会自动读取 `.env` 文件。

可选的几何依赖：

```powershell
python -m pip install -e ".[geometry]"
```

Mock 数据仅用于协议与本地开发验证，不能作为真实 E3D 项目状态、Schema、工程结果或写入成功的证据。

## MCP 客户端配置示例

将 `<repo-path>` 替换为仓库绝对路径。Codex `config.toml` 示例：

```toml
[mcp_servers.aveva-e3d]
command = "python"
args = ["-m", "aveva_e3d_mcp"]
cwd = "<repo-path>"
env = { E3D_MCP_MODE = "mock", PYTHONIOENCODING = "utf-8", PYTHONPATH = "<repo-path>/src" }
```

如果环境中已经通过 `python -m pip install -e .` 安装了本项目，可以省略示例中的 `PYTHONPATH`；否则保留它并将 `<repo-path>` 替换为仓库绝对路径。Claude Desktop 等使用 JSON 配置的客户端也应运行相同命令，并将相同环境变量放入 MCP server 配置。

## 连接 E3D 2.1

本仓库提供 E3D21 原生 Addin 源码。先在安装了兼容 E3D 2.1 SDK 的 Windows 环境构建：

```powershell
$env:E3D21_ROOT = "C:\Program Files (x86)\AVEVA\Everything3D2.10"
dotnet build bridge/e3d21/E3d21HttpAdapter.csproj -c Release -p:E3D21_ROOT="$env:E3D21_ROOT"
```

构建只生成 DLL；它**不会**自动向 E3D 注册、部署或加载 Addin。请按本机 E3D 安装和组织流程注册并加载 `E3d21AiAddin.dll`，确认 Addin 已在 E3D 进程内启动后，再配置 MCP server：

```text
E3D_MCP_MODE=bridge
E3D_MCP_VERSION=E3D21
E3D_BRIDGE_URL=http://127.0.0.1:8766
E3D_MCP_ALLOW_COMMANDS=false
```

E3D21 Addin 默认监听 `127.0.0.1:8766`，可通过 Addin 进程环境变量 `E3D21_MCP_BRIDGE_PORT` 修改。Python MCP server 通用的 bridge URL 默认值为 `127.0.0.1:8765`，因此连接 E3D21 时应显式设为 `8766`（或与 Addin 实际端口一致）。每个 MCP 进程只服务所选的一个 E3D 版本；虽然 Python 配置可选择多个 bridge 地址，本仓库目前提供的原生 Addin 源码针对 E3D 2.1。

### Bridge 安全边界

E3D21 HTTP listener 只绑定 loopback 地址 `127.0.0.1`，不提供请求认证。仅供同一台机器上可信的 MCP server 使用；不要通过反向代理、端口转发或防火墙规则把它暴露给其他主机或不可信网络。保持 `E3D_MCP_ALLOW_COMMANDS=false`，除非你明确需要对应的模型/PML 写入能力并理解其后果。开启权限不代表目标可写、操作安全或结果已通过工程验证；`dry_run` 也不等同于 PML 语法校验或真实执行。

## 测试

在仓库根目录安装 pytest 并运行离线测试：

```powershell
python -m pip install pytest
python -B -m pytest -q tests
```

测试包含 Python 行为与 HTTP/native 合同检查；托管 C# harness 使用模拟对象，不需要连接正在运行的 E3D。自动测试通过不代表 Addin 已部署，也不替代目标 E3D 会话中的验收。

## PML 只读调试面板

`pml/e3d21/CodexAiDebug/` 包含一个可选的只读 PML 面板，显示当前 CE 的类型、名称和引用。它是简单诊断面板，不是 bridge 健康检查、权限检查或数据库就绪证明；面板上的 bridge 地址是固定显示文本，不会自动检测实际监听端口。加载方式：

```text
pml rehash all
pml load form !!codexAiDebug
show !!codexAiDebug centred
```

## 仓库结构

- `src/aveva_e3d_mcp/`：MCP server、配置、bridge transport 和工具实现。
- `bridge/e3d21/`：E3D 2.1 .NET Framework 4.8 Addin 与 HTTP adapter 源码。
- `pml/e3d21/`：只读调试面板源文件。
- `tests/`：离线 Python 测试、托管 C# harness 和合成 fixture。

本仓库不包含真实工程回执/图像、机器专用 profiles、构建产物、AVEVA SDK 或 E3D runtime。请勿提交 `.env`、本机路径、日志、工程数据、二进制或含有访问凭据的配置。

## 许可证

本项目采用 Apache License 2.0，见 [`LICENSE`](LICENSE)。AVEVA SDK 和运行时不包含在仓库中，仍受其各自许可条款约束。
