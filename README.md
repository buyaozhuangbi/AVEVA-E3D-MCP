# AVEVA E3D MCP Server

让 AI 助手能直接读取 E3D 的模型信息、协助做设计调试，也能按预览结果执行受控建模。项目由 Python MCP 服务和运行在 E3D 2.1 里的 .NET Addin 组成，连接真实工程时通过本机 bridge 调用 E3D 原生能力。

## 能力介绍

- **看懂模型和工程状态**：读取设备、管道、元件层级、属性、连接点、数据库身份和当前会话信息；结果来自 E3D，不靠模型猜测。
- **辅助管道布置和落模**：根据中心线路径规划直管与弯头，支持创建原生 PIPE、BRAN、ELBO 和 E3D 自动生成的 TUBI，并在执行前预览、执行后读回核对。
- **协助设备建模**：检查设备结构和可建类型，预览后创建或复制受支持的元素，并核对实际写入结果；也能查看设备位置和外形。
- **让多个 Agent 一起做建模任务**：不同 Agent 可以各自准备管道或设备任务，再由一个 workflow 检查依赖并按顺序执行。每一步都会重新预览和核对回执，失败或结果不确定时会停下来，不自动重放。
- **推进 PARAGON 元件库建设**：浏览 CATA、检查目录 Schema、创建或复制 SCOM、更新 PARAM，并追踪 PTSE、GMSE 等引用。
- **调试 E3D 插件和自动化**：查看连接状态、日志和 E3D 消息，执行受控 PML，检查命令状态与原生诊断证据。
- **检查几何并整理视图**：读取尺寸、体积和几何关系，生成结构化图纸预览，或查看和调整当前 E3D 视图。

写入会经过各工具自己的预览、执行和原生读回流程。默认关闭命令执行；打开写入权限前，请确认工程、目标元素和预览结果。自动化不会替代工程师对规格、净距和设计适用性的判断。

## 快速开始

需要 Python 3.10 或更新版本。在仓库目录创建虚拟环境并安装：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
$env:E3D_MCP_MODE = "mock"
python -m aveva_e3d_mcp
```

Mock 模式不需要启动 E3D，适合连接 MCP 客户端和查看工具。Codex、Claude Desktop、Cursor 等客户端可以用 stdio 启动服务；真实 E3D 连接步骤见[下方说明](#连接-e3d-21)。

要连接 E3D 2.1，需要在安装了兼容 E3D SDK 的 Windows 环境构建并加载仓库中的 Addin，再把 MCP 模式设为 `bridge`。步骤和端口配置见下方文档。

## 相关说明

- [连接 E3D 2.1 与构建 Addin](#连接-e3d-21)
- [多 Agent 建模工作流](docs/modeling-team-workflows.md)
- [原生管道落模](docs/piping-native-route.md)
- [PARAGON 元件库工作流](docs/paragon-library-workflow.md)
- [PARAGON CATA 建库核验](docs/paragon-database-creation.md)
- [更新说明（逐版本）](CHANGELOG.md)

## 连接 E3D 2.1

先在安装了兼容 E3D 2.1 SDK 的 Windows 环境构建：

```powershell
$env:E3D21_ROOT = "C:\Program Files (x86)\AVEVA\Everything3D2.10"
dotnet build bridge/e3d21/E3d21HttpAdapter.csproj -c Release -p:E3D21_ROOT="$env:E3D21_ROOT"
```

构建只生成 DLL，不会替你在 E3D 注册或加载 Addin。按本机安装流程加载 `E3d21AiAddin.dll`，然后配置 MCP 服务：

```text
E3D_MCP_MODE=bridge
E3D_MCP_VERSION=E3D21
E3D_BRIDGE_URL=http://127.0.0.1:8766
E3D_MCP_ALLOW_COMMANDS=false
```

Addin 默认只监听本机 `127.0.0.1:8766`。如果要执行模型写入或 PML，先读对应工具的预览和执行说明，再按需设置 `E3D_MCP_ALLOW_COMMANDS=true`。

## 验证

离线运行测试：

```powershell
python -m pip install pytest
python -B -m pytest -q tests
```

在仓库根目录省略 `tests` 参数时，也会按 `pyproject.toml` 的配置只收集正式测试目录。

测试会检查 MCP 工具、HTTP/native 合同和模拟 SDK 调用；通过测试不代表 Addin 已在目标 E3D 工程部署，也不能代替现场验收。

## 项目组成

- `src/aveva_e3d_mcp/`：Python MCP 服务和工具实现。
- `bridge/e3d21/`：E3D 2.1 Addin 与本机 HTTP bridge 源码。
- `pml/e3d21/`：可选的只读 PML 调试面板。
- `docs/`：管道、设备、PARAGON 和调试工作流说明。
- `tests/`：Python 行为测试和托管 native 测试程序。

AVEVA SDK 和 E3D runtime 不随仓库分发。请勿提交工程数据、日志、本机路径或访问凭据。

## 许可证

本项目采用 Apache License 2.0，见 [`LICENSE`](LICENSE)。AVEVA SDK 和运行时仍受其各自许可条款约束。
