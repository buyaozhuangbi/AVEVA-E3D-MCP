# AVEVA E3D MCP Server

面向 AVEVA Everything3D（E3D）的模型开发与调试 MCP 服务。Codex、Claude Desktop 和 Cursor 等客户端通过 stdio 调用 Python 服务。连接真实工程时，Python 服务通过本机 HTTP 与 E3D 2.1 进程内的 .NET Addin 通信。

当前版本为 **0.2.1**，注册 **122 个 MCP 工具**。使用 `tools/list` 查看工具定义，使用 `e3d_status` 检查连接和已加载 Addin 的功能。[版本更新](#版本更新)

## 能力概览

| 领域 | 主要能力 |
| --- | --- |
| 工程与诊断 | 检查会话、项目、MDB 和当前元素，读取模型层级、属性、Schema 与数据库状态。 |
| 设备与管道 | 检查设备和管道成员、实际连接点、规格与连接关系，生成只读建模计划或排查证据。 |
| PARAGON 目录 | 读取 Catalogue 结构、Schema 和 SCOM 模板，支持同 CATE 模板复制、使用现有点集与型集直接建模、按规范生成和 PARAM 更新。 |
| 数据库准备 | 检查当前 MDB 的库名和 DBNO 冲突，提供 CATA 建库字段和建库后核验步骤。数据库由用户在 ADMIN 中创建并加入目标 MDB。 |
| 几何与图纸 | 查询尺寸、空间关系和体积，生成网格、剖面、设备布置图及 PNG、SVG 或 DXF。 |
| 模型编辑与调试 | 创建、复制和修改受支持的原生元素，读取或调整显示状态，执行 PML 命令与脚本。 |

PARAGON 写入工具需要支持对应接口的 Addin。先预览并检查回执，再使用预览返回的会话、构建和修订信息显式执行。离线合同测试覆盖接口和回读逻辑，工程结果需在目标 E3D 会话中验收。

## 版本更新

### v0.2.1

- **直接新建 SCOM**。`e3d_paragon_component_create` 在现有 CATE 下新建元件，引用现有 PTSE 和 GMSE，并写入完整数值 `PARAM` 数组。执行后回读新 REF、归属、`PTREF`、`GMREF` 和参数。使用 `e3d_paragon_component_create_status` 查询原请求回执。[直接新建 SCOM](docs/paragon-component-create.md)
- **CATA 建库交接**。`e3d_database_preflight` 提供 ADMIN 表单字段、`WITH CATA` 根元素命令模板及建库后的三步只读核验，覆盖当前 MDB 中的库身份、TEAM 归属和 Catalogue 根。[建库操作手册](docs/paragon-database-creation.md)

[查看 v0.2.1 源码](https://github.com/buyaozhuangbi/AVEVA-E3D-MCP/tree/v0.2.1) · [查看 v0.2.0 至 v0.2.1 的变更](https://github.com/buyaozhuangbi/AVEVA-E3D-MCP/compare/v0.2.0...v0.2.1)

### v0.2.0

- **按规范生成元件**。`e3d_paragon_generate_from_standard` 支持通过内置 `SPEC`、`SPCO` 和 `CATREF` 选取模板，也支持根据用户提供的外部规范、条款或表格位置及尺寸到 `PARAM` 的映射生成元件。[按规范生成](docs/paragon-standard-generation.md)
- **模板复制与参数更新**。支持在同一 CATE 下复制现有 SCOM，单独写入并回读完整数值 `PARAM` 数组，并查询各写入请求的状态。[模板复制](docs/paragon-component-generation.md) · [PARAM 更新](docs/paragon-component-parameters.md)
- **管道与目录引用追踪**。从显式 DBREF 出发，读取 Design、Catalogue 和 Property 对象间实际观察到的引用。[引用追踪](docs/paragon-reference-trace.md)

[查看 v0.2.0 源码](https://github.com/buyaozhuangbi/AVEVA-E3D-MCP/tree/v0.2.0)

## 要求

- Python 3.10+ 运行 MCP server。
- `pytest` 用于 Python 测试。
- 要构建 E3D21 Addin，需要 Windows 上兼容的 AVEVA E3D 2.1 安装/SDK 和 .NET Framework 4.8 targeting pack。AVEVA SDK、E3D runtime 和安装文件不随仓库分发。
- 几何布尔相关可选工具需要额外安装 `geometry` 依赖。

## 快速开始

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

### Codex 客户端配置

将 `<repo-path>` 替换为仓库绝对路径，并把以下配置加入 Codex 的 `config.toml`。

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

E3D21 HTTP listener 绑定 `127.0.0.1`，无请求认证，供同机可信的 MCP 服务访问。MCP 服务默认禁用命令执行。需要模型写入或 PML 执行时，按工具文档确认环境与参数，再启用 `E3D_MCP_ALLOW_COMMANDS`。

## 测试

MCP 服务支持 Python 3.10+；测试辅助客户端使用 `tomllib`，运行完整测试集需要 Python 3.11+。在仓库根目录安装 pytest 并运行离线测试：

```powershell
python -m pip install pytest
python -B -m pytest -q tests
```

测试包含 Python 行为与 HTTP/native 合同检查；托管 C# harness 使用模拟对象，不需要连接正在运行的 E3D。自动测试通过不代表 Addin 已部署，也不替代目标 E3D 会话中的验收。

## 仓库结构

- `src/aveva_e3d_mcp/` 包含 Python 服务、桥接客户端和工具实现。
- `bridge/e3d21/` 包含 E3D 2.1 Addin 与 HTTP adapter 的 .NET Framework 4.8 源码。
- `pml/e3d21/` 包含显示当前元素类型、名称与引用的可选只读面板。[面板安装说明](pml/e3d21/CodexAiDebug/README.md)
- `docs/` 包含 PARAGON 工作流手册，`tests/` 包含离线 Python 测试、托管 C# 测试程序及合成测试数据。

本仓库不包含真实工程回执/图像、机器专用 profiles、构建产物、AVEVA SDK 或 E3D runtime。请勿提交 `.env`、本机路径、日志、工程数据、二进制或含有访问凭据的配置。

## 许可证

本项目采用 Apache License 2.0，见 [`LICENSE`](LICENSE)。AVEVA SDK 和运行时不包含在仓库中，仍受其各自许可条款约束。
