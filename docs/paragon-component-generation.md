# PARAGON 元件生成：同 CATE 模板复制

`e3d_paragon_component_copy` 实现一条生成新 SCOM 的受控执行路径：从现有 Catalogue SCOM 叶模板复制到其原 CATE，指定新名称，并独立回读新 REF 和规定范围内的数据。它与只生成草稿的 `e3d_paragon_generation_plan` 是不同工具。首个 recipe 固定为 `same_category_scom_copy_v1`；不接受用户 PML 源码、其他 backend 或参数修改。

本接口的代码、HTTP、故障屏障和回读算法可离线验证；离线测试不能证明目标 E3D 安装中精确 PML 语法、运行时复制语义或工程结果。使用前需要在隔离 Catalogue 中进行实机验收。本轮开发不自动部署、重载或运行这些命令。数据库仍由用户在 ADMIN 中配置。

## 1. 获取执行预览

需要明确的源模板和父 CATE 数值引用。以下均为示例，须替换为实际对象：

```json
{
  "name": "e3d_paragon_component_copy",
  "arguments": {
    "source_refno": "=2/1",
    "parent_refno": "=2/2",
    "name": "/EXAMPLE-SCOM-002",
    "dry_run": true
  }
}
```

预览不会运行 PML。只有当前 PARAGON/81、源与父对象、名称和必需模板数据足够时，才返回 `status=prepared` 和 `scoped_revision`。不能把 `blocked`、部分读取、缺失字段或一个计划 fingerprint 作为执行许可。

输入规则：

- `source_refno` 和 `parent_refno` 必须是不同的规范数值 DBREF，数据库编号为正 Int32、元素编号为非负 Int32；拒绝名称、CE、空白和前导零。
- `name` 最多 50 个 ASCII 字符，含开头 `/`。每个斜杠分段以字母开头，其余仅允许字母、数字、下划线和连字符；拒绝双斜杠、末尾斜杠、命令或通配符。
- `recipe` 省略时取固定 recipe；`dry_run` 默认 `true`。
- 预览禁止携带 `request_id`、`expected_session_id`、`expected_bridge_build` 或 `expected_revision`，即使其值为 null。

## 2. 显式生成并核对

执行需要 MCP 进程的 `E3D_MCP_ALLOW_COMMANDS=true`、一个由调用方分配且不重复的规范小写 UUID，以及从同一预览回执取得的 session、build 和 scoped revision。服务不会替你读取健康状态、刷新 token 或生成另一个 ID 后重发。

```json
{
  "name": "e3d_paragon_component_copy",
  "arguments": {
    "source_refno": "=2/1",
    "parent_refno": "=2/2",
    "name": "/EXAMPLE-SCOM-002",
    "recipe": "same_category_scom_copy_v1",
    "dry_run": false,
    "request_id": "00000000-0000-0000-0000-000000000001",
    "expected_session_id": "<来自预览的 session_id>",
    "expected_bridge_build": "<来自预览的 bridge_build UUID>",
    "expected_revision": "<来自预览的 64 位小写 scoped_revision>"
  }
}
```

执行前在唯一 E3D UI 回调中重新核对 PARAGON/81、Project/MDB 对象实例、typed CE、源和父对象、完整有序父成员、Catalogue 名称查询及模板必需切片。源和新对象均要求实际类型与基础类型为 SCOM；父对象要求 CATE，且源的真实 owner 必须等于该父对象。SDK creatability 只是当前观察，不能代替授权。

`scoped_revision` 绑定本进程内 Project/MDB 的不透明实例标识和规定读取范围；同名但换实例也会冲突。它不是数据库锁、事务、全属性 clone fingerprint 或权限证明。发生冲突后本次请求 ID 仍已保留，不会重复派发执行。

执行只使用服务生成的固定两行宏。原生命令返回后，在没有 native fault 的同一个 UI 回调中回读。`status=executed` 需要同时核实：

- 新对象的规范 REF、Catalogue 数据库类型、基础/实际 SCOM 类型、新名称、真实 CATE owner 和叶状态；
- 原有父成员顺序保留，并且只追加该新对象；
- 源模板在规定范围内未变，完整有限 DOUBLE PARAM 值按原顺序相等；
- 固定七引用中实际支持且完整观察的部分按形状、顺序和类型化身份相等；明确 unsupported 的属性不计入比较，unknown 不能当作相等；
- 前后规定上下文一致、回读完成、没有故障或未解释的错误。

父成员在执行前最多读取 100 项并确认终止，执行后允许 101 项并确认终止。PARAM 最多 128 项，单个引用数组最多 16 项；SDK 数组可能先整体物化，4096 项检查上限不代表 native 分配预算。仅 `unit_kind` 元数据读取失败不会抹掉完整数值证据；单位本身仍不因此被验证。

已确认 SDK 空引用与未知不同：只有原生对象明确 `IsNull=true` 且原生 REF 为 `[0,0]`，才可作为已观察的未设置引用比较。CLR null、读取异常、无效 REF 或未读取字段都保留 unknown。至少需要一个受支持引用属性的已知值切片；不能把没有覆盖的参考集声明为已核验。

## 3. 超时或失败后查询原请求

写入失败可能已经留下新对象。不要因超时、断开或错误换一个 request ID 重试，先查询原请求：

```json
{
  "name": "e3d_paragon_component_copy_status",
  "arguments": {
    "request_id": "00000000-0000-0000-0000-000000000001",
    "expected_session_id": "<原请求的 session_id>"
  }
}
```

查询只访问托管内存账本，不查询 SDK、忙状态或当前工程；native fault 后仍可读取已保存事实。`accepted`、`running`、`completed` 是账本阶段；`completed` 表示最终回执已保存，不保证生成成功。`unknown_or_expired` 不证明此前没有执行。

账本保留最多 64 条回执，已完成回执最多保留 30 分钟；accepted/running 不被驱逐。每会话最多保留 65536 个已用 ID，即使回执过期也不释放该 ID。完整回执的逻辑 UTF-8 预算是 256 KiB，超限时仍保留独立 critical summary，包括是否尝试创建、Run 是否返回、新 REF 是否已验证、回读与故障状态。账本不跨会话持久化，也不承诺跨其他 API 的全局去重。

失败回执保留 `progress`、已读取的 `before/after`、错误和 journal。`native_run_returned` 与 `native_run_success` 是命令事实；`known_new_refno_verified` 和 `readback_complete` 是另外的回读事实。`this_submission_executed` 表示本次进入了 native Run 阶段，不等于新对象已完成核验。所有路径固定 `retry_safe=false`，native fault 后立即停止 SDK 访问。

接口不自动删除残留、Undo、恢复 CE、保存工作、切换模块或重试。生成会改变 CE；需要人工检查实际结果和是否保存。数值/引用的有限核对不证明 word parameters、隐藏属性、全部引用、几何等价或工程适用性。

## HTTP 接线

- `POST /api/v1/paragon/component-copy`：严格 JSON，最多 8192 原始 UTF-8 字节，无 query；成功和失败均绑定服务器 session 与 Addin MVID。
- `GET /api/v1/paragon/component-copy/status`：只接受一次 `request_id` 和 `expected_session_id` query，不接受 body。会话不匹配拒绝，不读取另一会话的账本。
- `/health.api_features` 在支持 optional interface 时声明 `paragon_component_copy` 和 `paragon_component_copy_status`。
- 不支持的旧 Addin 返回 404，错误 method 返回 405；不会回退到通用 PML 或其他复制接口。
