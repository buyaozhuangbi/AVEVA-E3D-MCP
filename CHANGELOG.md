# 更新说明

按版本记录用户可见的能力变化。后续版本只在本文件新增更新记录；README 保持为长期有效的项目能力介绍。

## v0.2.3 — 2026-11-25

- **支持多 Agent 协同建模**：新增 `e3d_modeling_team_plan` 和 `e3d_modeling_team_execute`，把多个 Agent 准备的管道或设备任务合并为一个有依赖顺序的 workflow。
- **集中检查并顺序写入**：workflow 会检查任务依赖和同级命名冲突；执行时逐项刷新原生预览、写入并核对回执。失败或结果不确定时停止后续任务，不自动重放或回滚。
- **覆盖范围清晰**：目前支持原生布管、设备创建和复制；不包含设备变换、自动连接、自动保存、碰撞检查或跨 MCP 进程锁。详细合同见[多 Agent 建模工作流](docs/modeling-team-workflows.md)。
- **工具目录**：mock 工具目录增加到 126 项。

[查看 v0.2.3 源码](https://github.com/buyaozhuangbi/AVEVA-E3D-MCP/tree/v0.2.3) · [比较 v0.2.2 到 v0.2.3](https://github.com/buyaozhuangbi/AVEVA-E3D-MCP/compare/v0.2.2...v0.2.3)

## v0.2.2

- **原生管道自动落模与独立恢复核验**：支持创建含四个空间弯头、五段隐式 TUBI 的路线，以及无弯头直管；创建后可对 PIPE、BRAN、ELBO 和 TUBI 做独立只读复查。此前版本的发布标签继续指向原发布源码。
- **管道和设备建模预览**：计划可为现有父级下的直接子项请求原生 `dry_run` 与 revision；依赖新建父级的步骤需取得真实 DBREF 后再预览。
- **CATA 身份核验**：处理 TEAM 原生 `NAMN` 名称标记，并改善 DB、DBLI、TEAM 归属关系的只读核验；身份清单支持最多 250 项并保留范围未知状态。

[查看 v0.2.2 源码](https://github.com/buyaozhuangbi/AVEVA-E3D-MCP/tree/v0.2.2) · [比较 v0.2.1 到 v0.2.2](https://github.com/buyaozhuangbi/AVEVA-E3D-MCP/compare/v0.2.1...v0.2.2)

## v0.2.1

- **直接新建 SCOM**：在现有 CATE 下创建元件，引用现有 PTSE 和 GMSE，并回读新 REF、归属、引用和数值 `PARAM`。[直接新建说明](docs/paragon-component-create.md)
- **CATA 建库交接**：提供 ADMIN 表单字段、`WITH CATA` 根元素命令模板，以及建库后的只读核验步骤。[CATA 建库说明](docs/paragon-database-creation.md)

[查看 v0.2.1 源码](https://github.com/buyaozhuangbi/AVEVA-E3D-MCP/tree/v0.2.1) · [比较 v0.2.0 到 v0.2.1](https://github.com/buyaozhuangbi/AVEVA-E3D-MCP/compare/v0.2.0...v0.2.1)

## v0.2.0

- **按规范生成 PARAGON 元件**：支持通过 E3D 内置 `SPEC`、`SPCO`、`CATREF` 选择模板，也支持明确映射外部规范尺寸到 `PARAM`。
- **复制模板并更新参数**：支持在同一 CATE 下复制现有 SCOM，更新并读回完整数值 `PARAM` 数组。
- **引用追踪**：从明确 DBREF 出发，读取 Design、Catalogue 和 Property 对象间实际观察到的引用。[引用追踪说明](docs/paragon-reference-trace.md)

[查看 v0.2.0 源码](https://github.com/buyaozhuangbi/AVEVA-E3D-MCP/tree/v0.2.0)
