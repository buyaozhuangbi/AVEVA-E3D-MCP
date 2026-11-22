# AVEVA E3D MCP Server

面向 AI 辅助开发与调试的 AVEVA E3D MCP 服务。Python MCP 进程通过 stdio 与 MCP 客户端通信；E3D21 原生适配器在 E3D 进程内运行，并通过本机 HTTP 提供受限的上下文、模型读取和操作接口。

当前目录提供 114 个 MCP 工具。工具定义由服务在 `tools/list` 中发布。实际可用能力取决于 E3D 版本、原生适配器功能和运行配置。

## 项目结构

- `src/aveva_e3d_mcp/`：MCP 服务、配置、bridge transport 与工具实现。
- `bridge/e3d21/`：E3D21 .NET Framework 4.8 原生适配器源代码。
- `pml/e3d21/`：只读调试面板 PML 源文件。
- `tests/`：Python 单元/合同测试、托管 C# harness 和离线 fixture。

AVEVA SDK 程序集和 E3D 安装文件不包含在仓库中。构建或运行 E3D21 适配器需要本机安装的兼容 E3D 2.1。

## 本地运行

需要 Python 3.10 或更新版本。安装 MCP 服务并在 mock 模式启动：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
$env:E3D_MCP_MODE = "mock"
python -m aveva_e3d_mcp
```

开发测试需要 pytest：

```powershell
python -m pip install pytest
python -m pytest -q tests
```

如需运行依赖几何布尔引擎的可选工具：

```powershell
python -m pip install -e ".[geometry]"
```

MCP 客户端配置应将 `PYTHONPATH` 指向仓库的 `src`，启动 `python -m aveva_e3d_mcp`。配置环境变量时可参考 [`.env.example`](.env.example)；服务从进程环境读取设置，不会自动加载 `.env` 文件。

## 连接 E3D21

先在本机 E3D 2.1 环境构建并按你的部署流程加载原生适配器。适配器构建示例：

```powershell
$env:E3D21_ROOT = "<本机 E3D 2.1 安装目录>"
dotnet build bridge/e3d21/E3d21HttpAdapter.csproj -c Release -p:E3D21_ROOT="$env:E3D21_ROOT"
```

然后在 MCP 客户端进程环境中设置对应版本和本机 bridge 地址：

```text
E3D_MCP_MODE=bridge
E3D_MCP_VERSION=E3D21
E3D_BRIDGE_URL=http://127.0.0.1:8766
E3D_MCP_ALLOW_COMMANDS=false
```

桥接模式需要 E3D 进程内适配器先运行。不要把本机安装路径、日志路径、访问凭据或真实项目数据提交到仓库。

## PARAGON 范围

PARAGON 目录接口读取现有 Catalogue 数据库、目录 schema 和 SCOM 元件模板。有限生成计划仅为同一 CATE 下复制现有 SCOM 模板生成可审阅草稿，不自动提交预览或执行 PML，也不创建数据库、CATE 或元件。Catalogue 数据库由用户在 E3D ADMIN 中配置；本服务不会替用户配置 ADMIN 或推断写权限。

任何模型写入都应由用户明确发起，并在目标 E3D 会话中独立核对结果。离线测试通过不代表已在真实工程中部署或验收。

## 安全配置

- 默认模式为 `mock`。
- `E3D_MCP_ALLOW_COMMANDS` 默认关闭；保持关闭，除非你明确配置并理解对应命令能力。
- bridge 默认只使用本机地址；不要将 E3D 原生 bridge 暴露到不可信网络。
- `.env`、profiles、本机日志、构建输出和 `artifacts/` 均由 `.gitignore` 排除。

## 许可证

本仓库沿用 GitHub 仓库中的 Apache License 2.0。第三方 AVEVA SDK 和运行时仍受其各自许可约束，不随本仓库分发。
## 仓库范围

仓库不包含 `artifacts/` 中的真实工程回执、图像和运行日志，也不包含机器专用 `profiles/`、构建输出或可能连接真实 E3D 并执行操作的本地验收脚本。依赖这些被排除内容的本机测试文件也不会提交；公开仓库保留可离线运行的源码、测试和合成 fixture。
