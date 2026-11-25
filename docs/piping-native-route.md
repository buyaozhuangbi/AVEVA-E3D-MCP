# 原生管道自动落模

`e3d_piping_route_create` 把显式中心线路径创建为新的原生 PIPE、BRAN、ELBO，以及 E3D 自动生成的隐式 TUBI。`e3d_piping_route_verify` 对现有管线进行独立只读核验。两者使用已有 E3D21 Addin 的读取和有请求日志的 PML 接口，无需替换运行中的 DLL。多 Agent 场景可经 `e3d_modeling_team_execute` 顺序提交并回读多项路线/设备作业。

## 支持范围

- 目标是明确的现有 Design ZONE。工具创建一个新 PIPE、一个 BRAN、0–16 个原生 ELBO，保留旧成员相对顺序；PML NEW 将新 PIPE 插入父级第一位。创建前父级最多 199 个直接成员，保证新增后仍能在 200 项有界读取内完成核验。
- 直管可以直接创建；转弯使用显式现有两端口、90°、无 CREF、无显式子项覆盖的固定目录 ELBO 模板。可在 WORLD 中旋转到空间方向。
- 从显式 BRAN 读取 SPEC 和 HSTU，从显式 ELBO 读取 SPRE、CATR、LSTU、ANGL、RADI、WORLD 姿态及 P1/P2。核对两种 SPCO 的原生 OWNER 链均归属同一 SPEC，并核对端点、模板及隐式管材的标称口径。
- 从模板的真实 P1/P2 位置和 PDIR 计算中心线半径及刚体变换。请求半径必须在位置容差内匹配模板实测半径；工具不通过修改 RADI 来改变目录几何。
- 每个弯头前后都需要正长度直段。位置容差在该写入工具中限制为 0.01–1 mm。模板口径和半径不符、非 90° 转弯、零长度直段及不完整原生证据均拒绝准备。
- 新 BRAN 的 HREF/TREF 显式保持 Nulref，HCON/TCON 为 OPEN，LHEAD/LTAIL 为 TRUE。选中的现有 P-point 仅用于定位，不改写已有对象或自动建立外部连接。

## 预览

以下是格式示例，REF 和路径点需要替换成当前工程的真实对象与坐标。先通过 `e3d_piping_route_plan` 或 `e3d_connection_points` 确认端点和路径，再调用：

```json
{
  "parent_refno": "=1/1",
  "pipe_name": "/AUTO-PIPE-001",
  "template_branch_refno": "=1/2",
  "template_elbow_refno": "=1/3",
  "endpoints": [
    {"refno": "=1/4", "expected_type": "NOZZ", "point_number": 1},
    {"refno": "=1/5", "expected_type": "NOZZ", "point_number": 1}
  ],
  "centerline": {
    "start_endpoint_index": 0,
    "end_endpoint_index": 1,
    "waypoints_world_mm": [[1000, 0, 0]],
    "bend_radius_mm": 100,
    "minimum_straight_mm": 50,
    "position_tolerance_mm": 0.01,
    "direction_tolerance_degrees": 0.1,
    "start_direction_sense": 1,
    "end_direction_sense": -1
  },
  "dry_run": true
}
```

模板 BRAN 和 ELBO 不要求来自同一根管线，但规格、管材和口径需要通过原生证据核验。全部输入 REF 必须是规范形式的数字 DBREF；PIPE 名称仅接受受限 ASCII 字母、数字、`_`、`-` 和 `/`，完整长度最多 36 字符。分支名自动为 `PIPE名/B1`，弯头名为 `PIPE名/E01` 等。工具不接受外部 PML 片段或调用方伪造的中心线计算回执。

成功预览的 `result.status` 为 `prepared`，包含 `revision`、`plan_revision`、`expected_session_id`、`native_source`、`source_sha256`、`centerline_plan`、`elbow_placements`、`planned_names` 和 `verification_request`。`revision` 绑定完整创建前状态；`plan_revision` 绑定会话、输入、来源几何、规格和目标路径，允许创建后的 CE 与父级其他成员发生变化。原生 dry-run 仅验证生成宏的编码，不证明 PML 语法或数据库写入必定成功。

## 执行与回读

保留相同的目标、模板、端点和中心线参数，将 `dry_run` 改为 `false`，并补充：

```json
{
  "expected_revision": "预览返回的64位小写十六进制修订号",
  "expected_session_id": "预览返回的会话ID",
  "request_id": "调用方生成的规范UUID"
}
```

执行需要 `E3D_MCP_ALLOW_COMMANDS=true`。服务重新读取状态并比较修订号，在原生宏的第一条创建命令前检查父级成员顺序、模板类型和规格引用、几何端点与模板 P-point 的 WORLD 位置/方向。宏只对新对象写属性与位姿；已有对象保留为只读来源。修订号和这些检查不构成数据库锁，也不冻结外部编辑。

成功时 `status=created_verified`、`success=true`、`readback_verified=true`。回读包括：

1. 新 PIPE 的明确数字 REF，父级只有这一个新成员，原有成员身份和相对顺序保留。
2. PIPE → BRAN → TUBI/ELBO 的完整有界原生成员序列。TUBI 与相邻组件共享 DBREF 时，按类型和顺序分别保留。
3. 新 PIPE 和 BRAN 的规格，以及 BRAN 的管材、标称口径、固定端点标志、OPEN 连接代码及空的外部引用。宏内另以 WORLD 坐标和方向验证 HPOS/TPOS/HDIR/TDIR。
4. 每个 ELBO 的原生 P1/P2 位置、方向、口径及 SPRE/CATR。
5. 每段隐式 TUBI 的原生 ITLEN、SPRE、ABOR/LBOR。通用属性的数字值仅在独立格式化值明确为毫米且两者一致时按毫米比较；不能核实单位时拒绝成功判定。

执行结束 CE 位于新 PIPE。工具不自动添加 Drawlist、调整相机、抓图、SAVEWORK、恢复 CE 或建立外部连接。

## 失败与重复请求

服务为每个执行 UUID 保留一次调用的结果，相同参数和 UUID 的后续调用返回原回执，不再次读模型或执行；同一 UUID 用于不同参数会被拒绝。原调用尚在进行时返回 `request_in_progress=true`。Python 记录上限为 64，已完成记录可能被淘汰，且不跨 MCP 进程重启。原生 PML 执行还使用已有的会话内请求日志拒绝已接受的 UUID。

提交后的超时、断连或无效响应保留 `outcome_uncertain`、`retry_safe=false` 和 `pml_script_request`，也保留原生回执报告的不确定状态。用 `e3d_pml_script_status` 及原 request ID/session 查询执行结果；这个状态查询读取的是原宏执行回执，不重新验证管道。已有模型可用下述核验工具继续观察。不得用新 UUID 自动重放不确定的创建。UUID 日志不是跨会话的永久幂等保证。

`failed_after_submission` 可能已经创建部分或全部元素。查看 `native_execution`、`planned_names` 和残留状态，再进行只读查询；不会自动删除已创建对象或回滚整个宏。某次宏完成后若回读失败，也保留失败状态，不把宏成功当成模型核验成功。

## 独立恢复核验

调用 `e3d_piping_route_verify`，保留原请求的六项路由参数，另外提供：

```json
{
  "pipe_refno": "=1/100",
  "expected_session_id": "原E3D会话ID",
  "expected_plan_revision": "原预览或创建回执的plan_revision"
}
```

成功创建的回执提供可直接传入的 `verification_request.arguments`。预览及失败回执同样保留这些输入和指纹，但 `required_after_discovery` 会提示补上 `pipe_refno`；先用只读查询确认残留的准确数字 REF。工具同时核对 REF、PIPE 名称与指定 ZONE 归属，避免仅凭同名对象归因。

核验允许 MCP 进程重启、用户切换 CE，以及核验开始前父级其他成员变化；不要求目标仍是第一个成员。开始核验后，观察到上下文或父级成员变化会停止成功判定。E3D 会话必须匹配；模板、端点或规格变化导致方案指纹不符时，不按旧方案宣称通过。指纹用于比较，不是签名或创建归属证明。

WORLD 头尾核验使用工具生成的完整 PML：只读明确 BRAN 的 HPOS/TPOS/HDIR/TDIR，转换为 WORLD，再比较期望值，不定位 CE 或写数据库。因此该只读工具仍要求 `E3D_MCP_ALLOW_COMMANDS=true`。其余检查通过已有 SDK 读取完成，包括 PIPE 规格、BRAN 固定/开口状态、完整成员序列、ELBO 的 PPOS/PDIR 和目录引用、每段 TUBI 的规格、ITLEN 和口径。所有读取仍是顺序观察，不是原子快照。

成功返回 `status=model_matches_plan`、`readback_verified=true`。传入指纹并匹配时 `plan_binding_verified=true`；省略 `expected_plan_revision` 仅与当前重新读取的来源和显式路径比较，`plan_binding_verified=false`。旧版回执没有指纹时也可以进行这种当前比较，但不能据此声称已绑定历史方案。

`creation_attribution_verified` 与 `readback.historical_parent_changes_verified` 始终为 `false`：当前模型符合期望，不证明由某次请求创建，也不证明历史上只新增了一个成员。核验不会改写创建工具的旧回执，也不把它转换成 `created_verified`。

失败返回 `verification_failed` 与分阶段错误；原生断连或回执不确定时返回 `verification_uncertain`，保留此次只读脚本自己的 `pml_script_request`。用其 IDs 查询这次断言宏，原创建宏仍使用原来的 IDs。工具不创建、修复、重新连接、清理或重放管线。

## 实机验证

在 E3D21、APS/ALL 工程中，使用实测约 38.1 mm 半径、50 mm 标称口径的目录 ELBO，创建了 `/CODEX-AUTO-PIPE-003`：

- PIPE `=2013286676/4017`，BRAN `=2013286676/4018`。
- 4 个 ELBO：`=2013286676/4019` 至 `=2013286676/4022`。
- 5 段原生 TUBI 长度约 233.800、423.800、623.800、423.800、223.800 mm。

该次完整调用返回 `created_verified`，重复同一 UUID 返回原结果，没有重复创建。调试阶段的 `/CODEX-AUTO-PIPE-001`、`/CODEX-AUTO-PIPE-002` 也保留在工程中；001 首次宏末尾验证语法失败，002 原生命令成功后因最初的父级顺序断言失败而保留失败回执。修复后已对 002 完成独立只读回查，未重放创建。

新增恢复核验已对 002、003 完成当前比较，以及在另一个 MCP 进程中携带比较指纹的复查。两次均保持原有 CE；003 复查时 CE 位于 002，仍通过核验。这些旧回执没有创建时的 `plan_revision`，此次指纹来自新的只读比较。

另外创建 `/CODEX-AUTO-PIPE-004`，PIPE `=2013286676/4023`、BRAN `=2013286676/4024`，无 ELBO，一段原生 TUBI 长约 1233.800 mm。创建完整返回 `created_verified`，相同 UUID 返回原结果；另一个 MCP 进程直接使用创建回执的 `verification_request.arguments`，返回 `model_matches_plan` 与 `plan_binding_verified=true`。该测试直管也保留在工程中。

以上验证覆盖原生对象和几何/规格回读，不等于工程连接、外径包络、路径自交或障碍物净距通过。首版不支持自动选择目录、任意角度或可变半径弯管、异径件、三通、阀门及与现有分支的自动连接。
