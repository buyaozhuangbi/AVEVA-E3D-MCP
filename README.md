# AVEVA E3D MCP Server

面向 AVEVA Everything3D（E3D）的模型开发与调试 MCP 服务。Codex、Claude Desktop 和 Cursor 等客户端通过 stdio 调用 Python 服务。连接真实工程时，Python 服务通过本机 HTTP 与 E3D 2.1 进程内的 .NET Addin 通信。

当前版本为 **0.2.2**，注册 **124 个 MCP 工具**。使用 `tools/list` 查看工具定义，使用 `e3d_status` 检查连接和已加载 Addin 的功能。[版本更新](#版本更新)

## 能力概览

服务把 Python MCP、E3D 进程内的 .NET Addin 和 E3D SDK 连接起来。Mock 模式可用于发现工具与离线开发。E3D 2.1 bridge 模式提供受支持的原生读取和写入。工具定义会随 Addin 的 `api_features`、当前运行模式和配置而变化，客户端可用 `tools/list` 查看可调用工具。

### 插件开发与调试

仓库包括 Python MCP 服务、E3D 2.1 .NET Framework 4.8 Addin/HTTP adapter，以及一个只读 PML 调试面板。Python 服务通过 stdio 接入 MCP 客户端，Addin 在 E3D 进程中把 SDK 工作派发到 UI 线程，并通过仅监听 `127.0.0.1` 的本机 HTTP bridge 返回结果。调试时可以从连接和 session/build 身份开始，查看 adapter 日志、E3D 消息、命令状态与 PML 输出，再检查单个元素的结构化诊断证据。

- **构建与加载**：使用本机 E3D SDK 构建 `bridge/e3d21/E3d21HttpAdapter.csproj`，再按 E3D 安装流程注册并加载 Addin。构建不会自动部署或加载插件。[E3D 连接步骤](#连接-e3d-21)
- **运行状态**：`e3d_status`、`e3d_context_snapshot`、`e3d_project_info`、`e3d_module_context` 检查版本、bridge、session、project、MDB、模块和 CE。
- **日志与消息**：`e3d_read_logs`、`e3d_read_messages`、`e3d_read_message_window` 读取 adapter 诊断、Addin 捕获的 E3D 消息及会话内有界输出窗口。
- **PML 调试**：`e3d_execute_pml` 处理单条命令，`e3d_execute_pml_batch` 按顺序执行命令，`e3d_execute_pml_script` 处理完整宏和 IF/DO 脚本。`dry_run` 检查输入和编码，不解析 PML 语法。用 `e3d_pml_script_status` 查询同一会话的原请求回执。
- **命令恢复**：`e3d_command_state` 查看运行或等待状态，`e3d_cancel_command` 请求取消挂起命令，`e3d_reenter_design` 在成功 SAVEWORK 后重新进入 DESIGN 并确认新 session。
- **PML 面板**：`pml/e3d21/CodexAiDebug/` 中的 `!!codexAiDebug` 面板显示当前元素的类型、名称和引用。[面板安装说明](pml/e3d21/CodexAiDebug/README.md)
- **离线验证**：`tests/` 包含 Python 行为测试及托管 C# HTTP/native harness，可在没有运行中 E3D 的情况下验证输入合同、响应、故障路径和模拟 SDK 调用。

真实 PML 执行需要将 `E3D_MCP_ALLOW_COMMANDS` 显式设为 `true`。超时或断连可能无法确认脚本是否已运行，应查询原 request ID；服务不会自动重放不确定的写入。网络层、Addin 加载状态、目标 E3D 版本和 `api_features` 都会影响现场可用性。

### 模型数据、工程上下文与 Schema

读取工具围绕明确的 REF、根节点或请求范围工作，返回原生类型、上下文边界、读取错误及截断情况。范围不足或 SDK 读取失败时保留未知状态，不能把未知当作不存在。

- **层级与搜索**：`e3d_query`、`e3d_element_snapshot`、`e3d_list_members`、`e3d_find_descendants` 查询引用、读取树形结构、分页浏览直接成员或在明确根下搜索。
- **属性与类型**：`e3d_read_attributes` 和 `e3d_read_attributes_batch` 读取格式化与类型化属性。`e3d_element_schema`、`e3d_validate_model` 检查合法子类型、属性元数据和已发布的尺寸规则。`e3d_element_access` 读取当前 session 和指定目标的访问、锁及 claim 观察。
- **数据库身份**：`e3d_database_state`、`e3d_database_identity_snapshot` 读取当前 MDB 的数据库和身份清单。`e3d_team_database_identity_manifest` 观察 TEAM、DBLI、DB 及其类型化 owner 关系。`e3d_database_preflight` 对 DESI/CATA 生成非执行候选并检查当前 MDB 的名称和 DBNO 冲突，可为 CATA 指定根名并生成 `WITH CATA` ADMIN handoff；CATA 结果还给出类型码、根名来源和根元素尚未可见前的明确未知状态。建库后核验见[说明](docs/paragon-database-creation.md)。
- **设备和管道清单**：`e3d_equipment_manifest` 读取 EQUI 下有界成员树，`e3d_pipe_member_manifest` 读取 PIPE/BRAN 成员，`e3d_piping_branch_state` 读取 BRAN 属性、规格、服务信息和直接成员。
- **空间和批量测量**：`e3d_spatial_info` 与 `e3d_transform_coordinates` 在明确坐标系下读取或转换坐标。`e3d_measure_elements_batch` 批量读取空间位置、原生 bounds 和体积。`e3d_database_state` 的完整性仅指当前 MDB 观察范围，不代表项目范围内完整目录。

### 设备建模

设备能力覆盖现有模型读取、属性与 Schema 探查、建模计划、受支持元素创建/复制/变换，以及设备位置图和选定几何轮廓图。

1. 用 `e3d_equipment_manifest` 读取明确的 EQUI 和有界成员树，查看父子索引、成员顺序、类型、属性及截断原因。
2. 用 `e3d_equipment_child_schema` 检查目标父级的当前 `IsCreatable` 观察、元素类型元数据，以及可选的现有同类型 exemplar 属性。它是只读 SDK 证据，不代表权限或新实例属性已验证。
3. 用 `e3d_equipment_model_plan` 对 EQUI 下最多 16 个明确的 create、copy、transform 步骤形成只读计划。设置 `include_native_previews=true` 可取得 SDK 的 `dry_run` 回执。上下文一致时，create 和 copy 步骤返回相应的 revision。
4. 对已准备的步骤，用 `native_preview.arguments` 作为实际调用参数，将 `dry_run` 改为 `false`。`e3d_create_element` 和 `e3d_copy_element` 需要对应的 revision。`e3d_set_transform` 按目标和坐标系执行位姿更新。执行后检查原生回读。
5. 用 `e3d_equipment_layout_drawing` 或 `e3d_equipment_layout_sheet` 检查明确列出的 EQUI 姿态和位置。需要所选模型几何轮廓时，可用 `e3d_equipment_outline_sheet` 或 `e3d_equipment_layout_tiles`，并显式提供来源几何。

设备计划以现有 EQUI 及其成员为范围。若上一步写入改变父级成员或坐标系，用新状态重新预览后续步骤。设备轮廓图使用显式选择的来源几何。

### 管道设计与连接检查

- `e3d_piping_branch_state`、`e3d_pipe_member_manifest` 读取 BRAN/PIPE 结构和有序成员。
- `e3d_connection_points` 读取明确元素的 PPLS/PPOS/PDIR 等原生连接点，`e3d_check_connection_pair` 对一对显式 P-point 进行 SDK 位置和方向观察。
- `e3d_piping_connectivity_snapshot` 在一次 UI 回调中组合有界 BRAN 状态、P-point 配对及有向 PCREF 追踪。`e3d_trace_piping_connectivity` 对显式起点读取有向 PCREF 链。
- `e3d_piping_specification_read` 读取规格引用的原生格式化和类型化字段。`e3d_piping_route_plan` 用成员、BRAN、连接点和 Schema 证据生成建模顺序。设置 `include_native_previews=true` 后，可为现有 PIPE 或 BRAN 下的计划直接子项取得原生创建预览。依赖新建父级或新元件属性的步骤，等拿到实际 DBREF 后再预览。
- **中心线与弯头空间规划**：`e3d_piping_route_plan` 的可选 `centerline` 从两个显式原生 P-point 和最多 32 个 WORLD 路径点计算直段长度、圆弧弯头切点、圆心、法向、转角及总长。调用方指定半径、最小直段、容差和端点 PDIR 方向关系；工具报告短段、折返、弯头切向长度重叠及端点方向不符。可传 `planned_members: []` 单独规划路径。读取完整性与 `centerline_plan.geometric_fit` 分别报告。[输入示例与结果说明](docs/piping-centerline-planning.md)
- **原生自动落模**：`e3d_piping_route_create` 从显式 BRAN 的 SPEC/HSTU 和固定目录 ELBO 模板创建新的 PIPE、BRAN、最多 16 个 90° 空间弯头及原生隐式 TUBI，也支持无弯头直管。预览返回生成的 PML 和修订号；执行前重查成员顺序、目录引用及端点，执行后核验弯头 PPOS/PDIR、规格和管段 ITLEN。独立新分支两端保持开口，现有端点作为几何定位依据。[原生落模流程](docs/piping-native-route.md)
- **落模恢复核验**：`e3d_piping_route_verify` 对明确数字 REF 的现有 PIPE 作独立只读复查，支持 MCP 重启和 CE 切换。创建回执的 `verification_request` 保留输入和方案指纹；核验重新检查 WORLD 头尾、层级、规格、弯头及 TUBI。WORLD 头尾使用自动生成的只读 PML，因此仍需命令权限。核验结果单独返回，原创建回执继续保留。

创建同一父级下的多个计划项时，每个 revision 都针对当前观测到的父级成员状态。成功创建会改变该状态；执行一步后，先刷新计划，再执行剩余同级项。

连接点相近不等于工程连接兼容。Schema 和只读规划结果仍需通过显式创建操作落模；原生自动落模也不提供工程连接或净距结论。`e3d_spatial_map_status` 当前暂停 native CheckSpatialMap，`e3d_native_clash_check` 暂停原生 clash 执行。不要把这两项状态当成已通过碰撞检查。

### PARAGON 元件与 Catalogue

- **目录读取**：`e3d_paragon_catalogue_manifest` 浏览显式 Catalogue 根的有界成员树，`e3d_paragon_catalogue_schema` 观察当前父级原生 child type 和属性 Schema，`e3d_paragon_component_template` 读取现有 SCOM 的身份、数值 PARAM 和有限引用集。
- **新建 SCOM**：`e3d_paragon_component_create` 在现有 CATE 下引用现有 PTSE 点集和 GMSE 型集，写入完整数值 PARAM 数组。执行后读取新 REF、归属、`PTREF`、`GMREF` 和参数。状态由 `e3d_paragon_component_create_status` 查询。[直接新建说明](docs/paragon-component-create.md)
- **模板复制**：`e3d_paragon_component_copy` 在同一 CATE 下复制显式 SCOM 模板并读回规定身份和数据。`e3d_paragon_component_copy_status` 查询原请求状态。[复制工作流](docs/paragon-component-generation.md)
- **按规范生成**：`e3d_paragon_generate_from_standard` 支持 E3D 内置 SPEC/SPCO/CATREF 链路和外部标准到 PARAM 的明确映射。`e3d_paragon_generation_plan` 生成只读复核草稿。[规范生成说明](docs/paragon-standard-generation.md)
- **参数更新**：`e3d_paragon_component_parameters_set` 对已存在的 SCOM 预览并更新完整数值 PARAM 数组，执行后按类型化数值回读。`e3d_paragon_component_parameters_status` 查询其账本。[PARAM 说明](docs/paragon-component-parameters.md)
- **引用追踪**：`e3d_paragon_reference_trace` 沿显式 Design、Catalogue 和 Property DBREF 路径读取类型化身份及引用证据。[追踪说明](docs/paragon-reference-trace.md)
- **建库流程**：按 CATA 核验、SECT/CATE 层级、PTSE/GMSE 定义、SCOM 创建的顺序操作，并明确哪些步骤当前只支持预览或需要现有定义。[PARAGON 元件库工作流](docs/paragon-library-workflow.md)

写入工具需要目标 Addin 暴露相应 feature，并要求显式预览和对应 session、build、revision、request ID。复用点集/型集不代表已验证点位求值、型集几何生成、单位或工程适用性。数据库和访问配置由用户在 ADMIN 管理，MCP 的 CATA 建库流程只准备 ADMIN 字段并指导建库后核验。

### 原生几何编辑与测量

- **元素操作**：`e3d_create_element`、`e3d_copy_element`、`e3d_delete_element` 创建、复制或删除支持的明确元素。`e3d_set_attribute` 和 `e3d_set_attributes` 更新受限属性，`e3d_set_transform` 改变明确对象的位姿。
- **板件**：`e3d_panel_geometry` 读取 PANE/PLOO/PAVE，`e3d_panel_curve_boundary` 推导边界曲线，`e3d_panel_loop_relations` 检查环之间关系，`e3d_create_panel` 创建 PANE，`e3d_update_panel` 更新顶点，`e3d_panel_mesh` 推导参数网格。
- **实体与 CSG**：`e3d_primitive_geometry`、`e3d_generated_csg` 读取原生几何及生成 CSG 描述。`e3d_update_primitive` 在支持类型上预览和更新参数化尺寸/位姿。`e3d_geometry_bounds` 读取原生 bounds，`e3d_geometry_volume` 读取原生 gross/net volume。
- **几何关系**：`e3d_geometry_relation`、`e3d_geometry_relation_batch` 读取 E3D 空间谓词，`e3d_geometry_overlap_candidates` 用 bounds 生成宽阶段重叠候选。它们返回原生结果或候选，不提供工程 clearance 结论。
- **网格工具**：`e3d_primitive_mesh`、`e3d_element_mesh`、`e3d_primitive_csg_mesh` 从支持元素参数推导三角网格。`e3d_measure_triangle_mesh`、`e3d_validate_triangle_mesh` 检查调用方提供的网格。网格是参数化近似，不是 E3D tessellation 或模型写入。
- **比较与检查**：`e3d_compare_element_meshes` 比较两份参数网格。`e3d_validate_model` 检查已发布的类型和尺寸规则。`e3d_native_clash_check` 当前处于暂停状态。

### 图纸、布置与视图

工具可返回结构化线段/来源证据、PNG 预览和按接口支持的 SVG/DXF。

- **通用投影和剖面**：`e3d_project_element_drawing`、`e3d_project_assembly_drawing`、`e3d_section_element_drawing`、`e3d_section_assembly_drawing` 从显式的支持类型推导投影或截面。`e3d_compose_drawing_sheet` 组合对齐视图，`e3d_compose_drawing_atlas` 创建多页图册。
- **板件边界图**：`e3d_project_panel_boundary_drawing`、`e3d_project_panel_assembly_boundary_drawing` 投影板件控制点环。`e3d_project_panel_curve_drawing`、`e3d_project_panel_curve_assembly_drawing` 输出线段/圆弧边界。
- **实体和材料图**：`e3d_project_primitive_csg_drawing` 投影受支持的实体与负几何参数差。`e3d_project_material_assembly_drawing`、`e3d_project_material_assembly_sheet`、`e3d_project_material_assembly_tiles` 联合投影明确选择的材料来源或分幅。
- **设备位置和外形**：`e3d_equipment_layout_drawing` 绘制设备原点与姿态轴，`e3d_equipment_layout_sheet` 输出位置表，`e3d_equipment_outline_sheet` 投影指定设备下的选定来源，`e3d_equipment_layout_tiles` 输出固定比例分幅。
- **喷嘴方向**：`e3d_nozzle_orientation_drawing`、`e3d_nozzle_datum_sheet`、`e3d_nozzle_axis_sheet` 依据显式 PPOS/PDIR 绘制方向和坐标差。`e3d_nozzle_outline_sheet` 在选定设备局部轴中组合喷嘴点与指定轮廓几何。
- **原生视图**：`e3d_view_state`、`e3d_view_clipping_state`、`e3d_observe_view` 读取相机、裁剪和跨回调视图观察。`e3d_capture_view` 返回当前视图图像，`e3d_set_view`、`e3d_set_camera` 设置相机，`e3d_fit_view` 适配当前 Drawlist，`e3d_restore_view` 显式恢复观察到的视图。
- **Drawlist 与选择**：`e3d_drawlist_snapshot`、`e3d_display_snapshot`、`e3d_display_state` 读取显示成员和状态。`e3d_set_drawlist_visual`、`e3d_set_primitive_visual` 更新已有显示项，`e3d_remove_drawlist_member` 移除显示成员。`e3d_graphical_selection` 读取图形选择，`e3d_set_graphical_selection` 预览并替换选择集。

派生图纸不创建 E3D Draft 元素，也不修改工程模型。参数网格、native bounds 的坐标框架、Boolean 语义、截面孔洞角色及图纸工程用途按工具合同保留其限制。

### 诊断与操作回执

`e3d_diagnose_element` 为一个明确元素组合有界快照、Schema、几何、显示和视图证据，可选保留完整诊断回执和 PNG。`e3d_diagnostic_receipt` 读取原回执，`e3d_compare_diagnostic_receipts` 比较两份已保留证据。它们用于检查差异和读取覆盖范围，不自动判断原因、时间顺序或工程影响。

### 写入通用流程

适用的元素、几何和 PARAGON 写入工具采用各自的 dry-run 预览、显式执行和原生读回。调用者应检查工具是否可用、输入目标、预览修订、执行回执及失败后的 residual state。写操作不是跨 API 事务。服务不会仅因超时或回读失败就自动重试、删除对象、保存工程或恢复 CE。默认保持 `E3D_MCP_ALLOW_COMMANDS=false`，只在明确需要时开启。

## 版本更新

### v0.2.2

当前开发分支在 v0.2.2 基础上追加原生管道自动落模与独立恢复核验。已在 E3D21 实测四个空间弯头、五段隐式 TUBI 的管线，以及无弯头直管；两种管线均完成原生回读，直管完成重复 UUID 检查和 MCP 重启后的方案指纹核验。发布标签仍指向原 v0.2.2 源码。

- **管道与设备建模预览**。管道和设备计划可为现有父级下的直接子项请求原生 `dry_run` 与 revision；后续步骤依赖新建父级的，需取得 DBREF 后再预览。每次创建会改变父级成员状态，继续前应刷新计划。
- **CATA 身份核验**。TEAM 原生 `NAMN` 的单个前导 `*` 作为名称标记处理，修复 DB → DBLI → TEAM 归属关系降级为未知的问题。身份清单上限提高到 250 项；CATA 建库后核验请求设置为 250，并继续保留分页、项目级唯一性和 MDB 记录等价性未知状态。[CATA 建库手册](docs/paragon-database-creation.md)

[查看 v0.2.2 源码](https://github.com/buyaozhuangbi/AVEVA-E3D-MCP/tree/v0.2.2) · [查看 v0.2.1 至 v0.2.2 的变更](https://github.com/buyaozhuangbi/AVEVA-E3D-MCP/compare/v0.2.1...v0.2.2)

### v0.2.1

- **直接新建 SCOM**。`e3d_paragon_component_create` 在现有 CATE 下新建元件，引用现有 PTSE 和 GMSE，并写入完整数值 `PARAM` 数组。执行后回读新 REF、归属、`PTREF`、`GMREF` 和参数。使用 `e3d_paragon_component_create_status` 查询原请求回执。[直接新建 SCOM](docs/paragon-component-create.md)
- **CATA 建库交接**。`e3d_database_preflight` 提供 ADMIN 表单字段、`WITH CATA` 根元素命令模板及建库后的四步只读核验，覆盖当前 MDB 中的库身份、TEAM 归属、Catalogue 根和根 Schema。[建库操作手册](docs/paragon-database-creation.md)

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
