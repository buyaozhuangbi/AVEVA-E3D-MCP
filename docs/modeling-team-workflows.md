# 多 Agent 自动布管与设备建模

`e3d_modeling_team_plan` 接收若干 Agent 以 JSON 提交的路线/设备任务；`e3d_modeling_team_execute` 用单个 MCP 工具调用依序刷新原生预览、落模和回读。Agent 可并行做只读勘察、目录/规格核对、中心线计算、设备子树分解及 proposal 起草；由调用方汇总为一个 workflow bundle 后进入 E3D。Agent 在 `agent_id` 中的名字只是调用方标签。

## 适用操作

- `piping_route`：`arguments` 是 `e3d_piping_route_create` 的基础预览参数，例如 ZONE、PIPE 名称、BRAN/ELBO 模板、端点和中心线。
- `equipment_create`：`arguments` 是 `e3d_create_element` 的属性、尺寸、名称和坐标系参数。
- `equipment_copy`：`arguments` 是 `e3d_copy_element` 的来源、名称和坐标系参数。

每个 task 都带唯一 `task_id`、`agent_id` 和唯一整数 `sequence`。依赖由 `depends_on`、`parent_task_id` 或 `source_task_id` 声明。拓扑排序时优先尊重依赖，再按 `sequence` 和 `task_id` 确定顺序。不同 Agent 提案为同一直接父级声明同名子项时，计划会阻止 workflow。

当前协调器不处理变换 task。`e3d_set_transform` 尚无原生 expected revision，不能像 create/copy 一样将并行 Agent 的目标变换变成可靠的 compare-and-set 更新。也不接受通用 PML、自动连接、SAVEWORK 或自动 clash 检查。

## 多 Agent 先计划

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

将 plan 使用的**相同** `workflow_id` 和完整 `tasks` 数组，连同计划返回的 `expected_session_id`、`expected_bridge_build`、`expected_workflow_revision` 传给 `e3d_modeling_team_execute`。

执行器在一条工具调用中获取本协调器实例的独占锁，然后按依赖顺序逐项执行：

1. 重查 session、build、project、MDB 和当前 CE。
2. 解析已完成父项/复制源的 native 数字 REF。
3. 对当前作业重新获取精确 create/copy/route 原生预览；前一项修改同级成员后不会复用过期 revision。
4. 单次提交原生操作，核对 native 成功标志、创建 REF、父成员、层级/复制身份、读回和 CE，然后再处理下一项。

成功总状态为 `completed`，每个 `task_receipts` 项会保留所属 Agent 标签、task 顺序、revision、原生 preview 和完整执行回执。失败和不确定结果会保留已完成前缀，并将其后项目标记为 `not_started`；不会自动回滚或以新 UUID 重放。再次提交同一 workflow ID 和相同内容会返回原始结果；workflow ID 用于不同内容会被拒绝。被拒绝的 workflow 应修正 proposal 并使用新 workflow ID 重新计划。

## 并发边界

这一流程把多 Agent 输出**同步为一个受控的串行写入序列**，不是同时在 E3D 中并发写。共享 E3D21 的 UI dispatcher、CE、父成员 revision 和命令状态要求由一个协调器推进实际写入。不同 MCP 进程仍能直接访问同一个 Addin；本工具的 Python 锁是进程内互斥，不是跨进程分布式 lease。其他客户端可能与 workflow 竞争，E3D 原生 busy guard 也可能拒绝请求。跨进程共享租约需要 Addin 层的 lease/持有者协议，本次接口不宣称实现了它。

Agent ID 是自报标签，不是身份认证或权限边界。`workflow_revision` 绑定输入与计划上下文；每个 task 的原生 revision 才保护单项 create/copy/route 当前状态，二者都不是数据库锁。执行期间回执会检查观察到的上下文变动，但多次 native callback 不是原子事务。已完成前缀保留，剩余任务停止；无自动删除、补偿、保存、原子回滚、图形净距或工程适用性判定。
