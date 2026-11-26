# 多 Agent 自动布管与设备建模

Agent 可以分别用 `e3d_modeling_team_submit` 提交路线或设备任务，再由协调器用 `e3d_modeling_team_plan` 封存。`e3d_modeling_team_execute` 根据冻结引用，在单个调用中依序刷新原生预览、落模和回读；`e3d_modeling_team_status` 查询收件、计划或执行回执。原有的一次性提交完整 `tasks` 的方式继续支持。

Agent 可并行做只读勘察、目录/规格核对、中心线计算、设备子树分解及 proposal 起草。这里的 Agent 名称是调用方标签，工具本身不会启动或认证 Agent。**分别提交的 Agent 必须访问同一个 MCP 进程**；多个独立 stdio MCP 实例之间不会共享提案或锁。

## 适用操作

- `piping_route`：`arguments` 是 `e3d_piping_route_create` 的基础预览参数，例如 ZONE、PIPE 名称、BRAN/ELBO 模板、端点和中心线。
- `equipment_create`：`arguments` 是 `e3d_create_element` 的属性、尺寸、名称和坐标系参数。
- `equipment_copy`：`arguments` 是 `e3d_copy_element` 的来源、名称和坐标系参数。
- `element_transform`：`arguments` 使用 `e3d_set_transform` 的 `refno`、位置/方向和 `wrt` 参数；不要传 `dry_run` 或 `expected_revision`。计划预览生成的 revision 会在执行前重新预览，并由协调器传入原生受保护的变换写入。

每个 task 都带唯一 `task_id`、`agent_id` 和整数 `sequence`。整包模式要求 `sequence` 全局唯一；分别提交模式只要求同一 Agent 内唯一。封存时按原始 `(sequence, agent_id.casefold(), task_id.casefold())` 稳定分配全局序号，因此不同 Agent 同时使用 `10` 不会冲突，提交先后也不影响结果。依赖由 `depends_on`、`parent_task_id`、`source_task_id` 或 `target_task_id` 声明；实际执行先满足依赖，再按全局序号排序。`target_task_id` 只用于 `element_transform`，表示把已核实的前序输出 REF 作为变换目标。不同 Agent 提案为同一直接父级声明同名子项时，计划会阻止 workflow。

协调器会串行规划并执行变换 task。已有目标用 `arguments.refno`；同一 workflow 中由前序创建/复制/路线任务产生的目标，用 `target_task_id`。后者的原生预览会等到前序回读出 REF 后再执行。每个变换在写入前单独重新预览，并把刚返回的原生 revision 交给 `e3d_set_transform`；执行回执须通过 setter、Command.Update、坐标框架身份、目标读回和 CE 检查。也不接受通用 PML、自动连接、SAVEWORK 或自动 clash 检查。

## 各 Agent 分别提交，再统一封存

每个 workflow 最多 16 个任务，每个 Agent 只能提交一份。由协调方分配共同的 `workflow_id`，各 Agent 使用自己的 `agent_id` 和 `submission_id`（规范小写 UUID），分别调用 `e3d_modeling_team_submit`。任务里的 `agent_id` 可以省略，由外层标签补上；如果填写，必须完全相同。收件只保存本地数据，不读取或修改 E3D。

例如，设备细节 Agent 可以先交一个依赖尚未到达的父项的提案：

```json
{
  "workflow_id": "6aa81b11-9d16-46ce-8e99-13f59abc4afe",
  "agent_id": "equipment-body",
  "submission_id": "b15bf4ce-3dbb-49ee-a680-241209aa8931",
  "tasks": [{
    "task_id": "body",
    "sequence": 10,
    "operation": "equipment_create",
    "parent_task_id": "equipment",
    "arguments": {
      "element_type": "BOX",
      "name": "/TEAM-EQUIPMENT-BODY",
      "attributes": {"XLEN": "1000mm", "YLEN": "800mm", "ZLEN": "1200mm"},
      "position": [0, 0, 0],
      "wrt": "OWNER"
    }
  }]
}
```

设备总体 Agent 用同一个 workflow ID，提交另一份提案：

```json
{
  "workflow_id": "6aa81b11-9d16-46ce-8e99-13f59abc4afe",
  "agent_id": "equipment-layout",
  "submission_id": "b15bf4ce-3dbb-49ee-a680-241209aa8932",
  "tasks": [{
    "task_id": "equipment",
    "sequence": 10,
    "operation": "equipment_create",
    "arguments": {
      "parent_refno": "=2013286676/600",
      "element_type": "EQUI",
      "name": "/TEAM-EQUIPMENT",
      "position": [0, 0, 0],
      "wrt": "WORLD"
    }
  }]
}
```

示例 REF 必须换成当前工程里已核对的合法父级。类型、属性、名称和位置仍需原生 Schema 与预览确认。

收件后，协调器调用 `e3d_modeling_team_plan`，明确列出这次预期参与的 Agent：

```json
{
  "workflow_id": "6aa81b11-9d16-46ce-8e99-13f59abc4afe",
  "seal": {"agent_ids": ["equipment-layout", "equipment-body"]}
}
```

名单按大小写无关方式比较，不允许重名。尚缺 Agent 或存在名单外的提交时，返回 `awaiting_submissions`、`missing_agents`、`unexpected_agents`，保持收件开放且不读取 E3D。名单吻合才一次性冻结，并检查跨 Agent 的 task 重名、未知依赖、循环和命名冲突，再读取原生计划。

成功计划保留 `proposal_revision`、`workflow_revision`、session/build guards 和完整 `frozen_tasks`。`frozen_tasks` 展示冻结参数、全局序号以及各 Agent 的 `submitted_sequence`，供协调方审阅；不是执行时需要再次上传的任务包。`proposal_revision` 绑定原始提案、参与名单和 submission IDs；`workflow_revision` 另外绑定规范化任务与 E3D 项目上下文。

执行时用下面的形状调用 `e3d_modeling_team_execute`，把占位值换成这次计划返回的实际值，**不传 `tasks`**：

```json
{
  "workflow_id": "6aa81b11-9d16-46ce-8e99-13f59abc4afe",
  "expected_proposal_revision": "<proposal_revision>",
  "expected_workflow_revision": "<workflow_revision>",
  "expected_session_id": "<expected_session_id>",
  "expected_bridge_build": "<expected_bridge_build>"
}
```

执行器使用本进程保存的冻结任务，要求三个上下文 guards 与原计划相同，并在现场重新核对。不能混入新 `tasks` 替换内容，也不能对已收件 workflow 绕回整包模式。封存后仅允许精确重复提交；任何新增 Agent、换 submission ID 或修改内容都会拒绝。重复 seal 返回原计划，正在规划时返回 `proposal_planning` 且 `success=false`，不会再发一次原生预览。

全局校验异常或规划被中断后，状态为 `proposal_plan_failed` / `proposal_state=plan_failed`；封存不会自动开放，同一 seal 也不会自动重新运行。原生预览返回不通过时则保留 `frozen` 的 blocked 计划，不能执行。修正后使用新 workflow ID 提交并重新计划。

`awaiting_submissions`、`proposal_planning` 和 blocked 计划的 `success=false` 会按现有 MCP 协议返回 `isError=true`；调用方应读取 `structuredContent.result` 中的状态和名单，不要因外层错误标志重新提交写入。被原生校验拒绝的任务会在 `ordered_tasks[].native_preview` 保留原始回执和具体错误。

相同 Agent、submission ID 和规范化内容的重复提交是幂等的；task 数组重排或补齐缺省字段不改变内容。每个 workflow 内，不同 Agent 不能共用一个 submission ID。保存的输入与返回快照相互独立，调用方修改回执不会改变冻结任务。

## 兼容方式：一次提交完整任务包

调用 `e3d_modeling_team_plan`，各 Agent 的 proposal 一起提交：

```json
{
  "workflow_id": "6aa81b11-9d16-46ce-8e99-13f59abc4afe",
  "tasks": [
    {
      "task_id": "route-a",
      "agent_id": "route-planner",
      "sequence": 10,
      "operation": "piping_route",
      "arguments": {
        "parent_refno": "=2013286676/490",
        "pipe_name": "/AI-ROUTE-A",
        "template_branch_refno": "=2013286676/1811",
        "template_elbow_refno": "=2013286676/1812",
        "endpoints": [
          {"refno": "=2013286676/1812", "expected_type": "ELBO", "point_number": 2},
          {"refno": "=2013286676/1813", "expected_type": "ELBO", "point_number": 1}
        ],
        "centerline": {
          "start_endpoint_index": 0,
          "end_endpoint_index": 1,
          "waypoints_world_mm": [[-315334, 301145.88, 100300], [-315334, 301145.88, 99600]],
          "bend_radius_mm": 38.1,
          "minimum_straight_mm": 50,
          "position_tolerance_mm": 0.01,
          "direction_tolerance_degrees": 0.1,
          "start_direction_sense": 1,
          "end_direction_sense": -1
        }
      }
    },
    {
      "task_id": "equipment-base",
      "agent_id": "equipment-planner",
      "sequence": 20,
      "operation": "equipment_create",
      "arguments": {
        "parent_refno": "=2013286676/600",
        "element_type": "BOX",
        "name": "/AI-EQUIPMENT",
        "attributes": {"XLEN": "1000mm", "YLEN": "800mm", "ZLEN": "1200mm"},
        "position": [0, 0, 0],
        "wrt": "WORLD"
      }
    },
    {
      "task_id": "equipment-child",
      "agent_id": "equipment-detail-planner",
      "sequence": 30,
      "operation": "equipment_create",
      "depends_on": ["equipment-base"],
      "parent_task_id": "equipment-base",
      "arguments": {
        "element_type": "CYLI",
        "name": "/AI-NOZZLE",
        "attributes": {"DIAM": "200mm", "HEIG": "300mm"},
        "position": [0, 0, 900],
        "wrt": "OWNER"
      }
    }
  ]
}
```

此示例展示输入形状，不保证示例中心线路径、元素类型或属性适用于实际工程。应先使用当前工程读取工具和原生 Schema 校验每个 Agent 的数据。

计划调用不会写模型。`status=ready` 时保留 `workflow_revision`、`expected_session_id`、`expected_bridge_build` 及每个可解析 task 的 native dry-run 回执。依赖新建父级/来源的步骤返回 `deferred_dependency_preview`，等拿到前序创建的数字 REF 后再执行原生预览。计划排序和名称冲突检查不代表空间无干涉；工程读数和模型几何仍需要独立校验。

## 单调用同步执行

分别提交模式使用前述冻结引用执行；整包兼容模式则将 plan 使用的**相同** `workflow_id` 和完整 `tasks` 数组，连同计划返回的 `expected_session_id`、`expected_bridge_build`、`expected_workflow_revision` 传给 `e3d_modeling_team_execute`，省略 `expected_proposal_revision`。两种输入形态互斥。

执行器在一条工具调用中获取本协调器实例的独占锁，然后按依赖顺序逐项执行：

1. 重查 session、build、project、MDB 和当前 CE。
2. 解析已完成父项/复制源的 native 数字 REF。
3. 对当前作业重新获取精确 create/copy/route/transform 原生预览；前一项修改同级成员或目标后不会复用过期 revision。变换的预览 revision 会用于同一项 guarded write。
4. 单次提交原生操作，核对 native 成功标志、创建 REF、父成员、层级/复制身份、读回和 CE，然后再处理下一项。
5. 所有任务核实后，再读一次最终上下文，比对 session、build、project、MDB 和最后已核实的 CE，匹配时才将整个 workflow 标记为 `completed`。

成功总状态为 `completed`，每个 `task_receipts` 项会保留所属 Agent 标签、task 顺序、revision、原生 preview 和完整执行回执。创建类任务用 `created_verified`，变换任务用 `transform_verified`；变换只回报目标 REF，不创建新元素。失败和不确定结果会保留已完成前缀，并将其后项目标记为 `not_started`；不会自动回滚或以新 UUID 重放。相同 workflow 仍在执行时，再次提交完全相同的请求会返回进度快照，包括已完成步骤、当前步骤和 MCP 写入工具是否已经派发，不会启动第二个写入者；完成后则返回最终回执。`execution_attempted` 只表示 MCP 写入处理器已被调用，不代表 E3D 已写入；`any_write_tool_call_dispatched` 是整个 workflow 的累计值，`current_task_write_tool_call_dispatched` 仅表示当前任务、两任务间为 `null`。`native_write_state` 会区分尚未派发、等待回执、已收到回执、已核实、等待最终上下文核验及结果不确定。

任务回执中的 `retry_safe` 只针对该 task 的写入处理器是否已经派发，不授权复用相同 workflow ID；同一 ID 永远返回原进度或终态。若 workflow 在任何写入派发前以 `preflight_rejected` 结束，且 `any_write_tool_call_dispatched=false`，可修正数据、重新规划，并以新 workflow ID 执行。已派发的任务不要盲目以新 ID 重放。

## 查询进度与回执

调用 `e3d_modeling_team_status` 并只提供 `workflow_id`，即可读取收件、封存、执行进度或最终回执，不必重新提交任务包，也不会访问 E3D。`proposal_state` 表示收件/封存状态，`workflow_state` 表示当前执行状态；执行开始后 `proposal_state=frozen` 仍保持不变。状态快照中的 `success` 不替代计划回执的 ready 判断。

正在执行时可查看已验证的前置任务、当前 task、写入工具是否已派发和任务回执状态；完成后返回同一 workflow 的终态回执。收件和执行记录合计最多保留 64 个 workflow；收件中、规划中、已冻结未执行和执行中的记录不会被静默淘汰。容量满且没有已执行终态可释放时，拒绝新 workflow。MCP 进程重启或旧的已完成记录被清理后会返回 `not_found`。`not_found` 表示本进程没有保留这条记录，执行状态仍为未知；先用 E3D 读取能力核对实际模型，再决定后续操作，不要盲目重放写入。

标准 stdio 服务目前逐条同步处理请求；一个 `execute` 尚未返回时，后续 `status` 会排队等候。进行中的快照可供同进程的并发嵌入调用读取，目前没有后台异步任务或 stdio 实时轮询接口。

## 失败后的安全恢复

- `preflight_rejected` 且 `any_write_tool_call_dispatched=false` 表示 workflow 在任何写入工具派发前被拒绝，包括第一个任务的原生预览不通过。已失败的 task 回执可以显示 `retry_safe=true`，只表示该 task 尚未派发；修正 proposal，重新计划并使用新的 workflow ID。相同 ID 不会重新执行。
- `partial_failure` 表示流程未完整结束且已知有任务失败或中断；可能已有完成前缀，也可能 `completed_count=0`。按每条 receipt 的 `status`、`native_write_state`、`output_refno` 和 `retry_safe` 判断哪些写入已核实、哪些 task 尚未派发。后续新 workflow 排除已核实的写入，并把已创建父级/来源改为显式 DBREF；对已派发但未核实的任务，先按原生回执和模型读取结果确认。
- `stopped_uncertain` 或 `workflow_uncertain` 表示至少有一个写入结果无法确认。先查询本 workflow 状态与操作专属回执，并用 E3D 原生读取核对残留状态；在确认结果前不要把该项放进新 workflow 重放。
- 对 `retry_safe=true` 且 `native_write_state=not_dispatched` 的 task，修正失败原因后可放入新 workflow；这个字段不授权重放相同 workflow ID。`workflow_context_unverified` 表示已执行且原生写入已核实的任务，其上下文观察失败或被中断；后续任务可能仍为 `not_started`，应逐条检查 task receipt。保留已核实的写入与 REF，先独立读回项目上下文再决定后续操作。
- 所有任务已核实时，若最终上下文已成功读取，但 session、build、project、MDB 或最后核实的 CE 与执行预期不匹配，流程返回 `workflow_context_changed`。已有失败或不确定状态保持不变；已核实的 task 和 REF 仍予以保留，不重放任何写入。最终核验路径中的 `final_context_matches_expected` 为 `true`、`false` 或 `null`，分别表示匹配、已观察到不匹配、未能完成核验；更早拒绝或中断时该字段可能缺省，缺省应视为未知。这不是数据库锁，也不保证观察之后上下文不再变化。

不要仅凭 MCP 外层 `ok` 字段判断写入结果；应检查内部 workflow 状态、`native_write_state` 和对应 task receipt。Agent proposal 的并行准备由调用方负责，MCP 工具本身只接收并协调已提交的数据，不会启动 Agent。

## 并发边界

这一流程把多 Agent 输出**同步为一个受控的串行写入序列**，不是同时在 E3D 中并发写。共享 E3D21 的 UI dispatcher、CE、父成员 revision 和命令状态要求由一个协调器推进实际写入。不同 MCP 进程仍能直接访问同一个 Addin；本工具的 Python 锁是进程内互斥，不是跨进程分布式 lease。其他客户端可能与 workflow 竞争，E3D 原生 busy guard 也可能拒绝请求。跨进程共享租约需要 Addin 层的 lease/持有者协议，本次接口不宣称实现了它。

Agent ID 是自报标签，不是身份认证或权限边界。`workflow_revision` 绑定输入与计划上下文；每个 task 的原生 revision 才保护单项 create/copy/route 当前状态，二者都不是数据库锁。执行期间回执会检查观察到的上下文变动，但多次 native callback 不是原子事务。已完成前缀保留，剩余任务停止；无自动删除、补偿、保存、原子回滚、图形净距或工程适用性判定。
