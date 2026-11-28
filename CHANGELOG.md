# 更新说明

按版本记录用户可见的能力变化。后续版本只在本文件新增更新记录；README 保持为长期有效的项目能力介绍。

## v0.2.7 — 2026-11-28

- **批量管道端点链接与路径优化**：新增只读批量规划，可指定各管线头尾 P-point、共享端点和路径 waypoints；按原生连接身份读取端点，生成并排序候选路径，再联合优化多条路线。结果提供可继续预览的路线草案，不执行模型写入或自动 CONNECT。[批量管道规划说明](docs/piping-batch-routing.md)
- **设备空间提示**：设备建模计划可比较候选设备与已知设备外形，报告可能重叠项；BOX 使用有向包围盒检查，其他边界情况按保守包围盒提示。[设备建模计划说明](docs/equipment-model-plan.md)
- **中心线自交检查**：管道中心线规划会检测非相邻输入线段的自交或相触，并返回诊断信息；此检查不代表障碍物或设计净距检查。
- **工具目录**：新增批量管道路径规划工具，并同步更新 mock 与兼容性清单。

[查看 v0.2.7 源码](https://github.com/buyaozhuangbi/AVEVA-E3D-MCP/tree/v0.2.7) · [比较 v0.2.6 到 v0.2.7](https://github.com/buyaozhuangbi/AVEVA-E3D-MCP/compare/v0.2.6...v0.2.7)

## v0.2.6 — 2026-11-27

- **重载验收可复现**：将 E3D SAVEWORK/DESIGN 重载校验脚本纳入仓库。DESIGN 后 E3D 选择新的合法当前元素会被记录为观察结果，不再误判为项目/MDB 重载失败；项目名、MDB 和连接状态仍严格核验。
- **发布元数据审计**：新增本地 wheel 元数据、目录版本和 RECORD 哈希一致性检查，防止旧的 egg-info 或历史 wheel 污染当前发布包。

[查看 v0.2.6 源码](https://github.com/buyaozhuangbi/AVEVA-E3D-MCP/tree/v0.2.6) · [比较 v0.2.5 到 v0.2.6](https://github.com/buyaozhuangbi/AVEVA-E3D-MCP/compare/v0.2.5...v0.2.6)

## v0.2.5 — 2026-11-27

- **批量读取元素祖先和数据库身份**：新增只读 `e3d_element_ancestry_batch`，在一次 E3D UI callback 中按输入顺序检查 1–16 个显式 REF，共用一份 MDB 数据库清单、Design WORLD frame 和前后上下文观察；每个目标保留独立部分结果。普通目标读取错误会继续处理其他目标，原生数据库故障会停止后续 SDK 读取并把尚未开始的项目标为 `not_started`。不回退到重复单项 HTTP 请求，不执行模型写入。[接口说明](docs/element-ancestry.md)
- **工具目录**：mock 工具目录增加到 130 项。

[查看 v0.2.5 源码](https://github.com/buyaozhuangbi/AVEVA-E3D-MCP/tree/v0.2.5) · [比较 v0.2.4 到 v0.2.5](https://github.com/buyaozhuangbi/AVEVA-E3D-MCP/compare/v0.2.4...v0.2.5)

## v0.2.4 — 2026-11-26

- **各 Agent 可分别交提案**：新增 `e3d_modeling_team_submit`；协调方按明确的 Agent 名单封存，再按冻结引用执行。计划保留完整任务和原始序号，支持跨 Agent 父项/复制来源依赖；封存后拒绝替换任务或混用整包接口，重复提交、封存和执行分别去重。
- **查询工作流进度和回执**：新增只读 `e3d_modeling_team_status`，按 workflow ID 查询收件、封存、已完成任务和当前任务快照；明确区分写入派发、收到原生回执、核实成功和结果不确定。收件与状态查询不访问 E3D，记录只在同一 MCP 进程内共享。
- **多 Agent workflow 支持变换任务**：新增 `element_transform`，可移动或定向既有目标，也可用 `target_task_id` 定位前序任务创建或复制的新元素。每项任务使用唯一 `sequence`，变换逐项重新预览、提交 revision，并核对 Command.Update、读回、坐标框架和 CE。[工作流说明](docs/modeling-team-workflows.md)
- **变换写入增加原生 revision 保护**：`e3d_set_transform` 的预览返回绑定目标、坐标系、MDB 和请求值的 64 位十六进制 token；执行时必须携带同一 token，过期时在 setter 前拒绝。独立 revisioned HTTP 路由不会向旧 bridge 的无保护接口回退；多 Design 数据库时绑定目标所属数据库的 WORLD。[变换 revision 说明](docs/element-transform-revision.md)
- **新增原生祖先与 MDB 身份查询**：`e3d_element_ancestry` 在单次只读回调中读取明确 REF 的 OWNER 链、raw/格式化 REF、Design WORLD 和有界 MDB 数据库清单；保留 extract 歧义和部分身份的未知状态。管道/设备创建 revision 同步核验完整 MDB、祖先和数据库/extract 身份，在分配元素前拒绝歧义。[祖先查询说明](docs/element-ancestry.md)
- **修正 E3D 2.1 的 WORLD 识别**：根据实机观察保留 `IsWorld` 原始值，但祖先遍历、创建预检和变换框架按类型短名、原始 REF 与数据库 WORLD 身份判定，避免提前停在 SITE 或拒绝真正的 WORL。
- **准确报告失败、中断和上下文变化**：保留原生失败预览、已核实 REF 和原生写入回执，区分尚未派发、写入中断、任务间中断及上下文未核实。所有任务核实后再次比较 session、build、project、MDB 和最后已核实的 CE；读取失败报告 `workflow_context_unverified`，观察到漂移报告 `workflow_context_changed`，不会误报完成或重放写入。
- **收紧原生故障屏障**：祖先查询的元素有效性、变换 revision 的属性/类型/坐标读取，以及共享 WORLD 和祖先身份预检均逐次检查原生故障；前一次 SDK 读取锁存故障后立即停止后续读取。
- **固定默认测试收集目录**：直接运行 `python -B -m pytest -q` 只收集 `tests/`，避免本地历史实验副本引发同名模块冲突。mock 工具目录增加到 129 项。

[查看 v0.2.4 源码](https://github.com/buyaozhuangbi/AVEVA-E3D-MCP/tree/v0.2.4) · [比较 v0.2.3 到 v0.2.4](https://github.com/buyaozhuangbi/AVEVA-E3D-MCP/compare/v0.2.3...v0.2.4)

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
