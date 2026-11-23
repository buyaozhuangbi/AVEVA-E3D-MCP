# PARAGON SCOM 参数化

`e3d_paragon_component_parameters_set` 对一个明确数值 REF 的 Catalogue SCOM 替换完整 PARAM 数组，并独立回读核对。它可接在[模板复制生成元件](paragon-component-generation.md)之后，也可用于明确选定的现有 SCOM。当前只支持 1–128 个有限 DOUBLE 值，数组长度必须与完整读取的现有 PARAM 相同。

接口已进行离线合同测试和真实 E3D21 SDK 编译验证；实际写入语义、几何结果和工程适用性仍需在目标 E3D 的隔离 Catalogue 中验收。数据库及访问配置由用户在 ADMIN 中完成。

## 预览参数

以下是调用结构示例；REF 和参数须替换为实际对象与适用的原生数值：

```json
{
  "name": "e3d_paragon_component_parameters_set",
  "arguments": {
    "refno": "=2/3",
    "parameters": [0.0, 80.0, 125.5],
    "dry_run": true
  }
}
```

预览在一个 E3D UI 回调中核对当前 PARAGON/81、数值 REF、Catalogue 数据库类型、基础和实际 SCOM 类型、叶状态、实际 CATE owner，以及 Project/MDB 对象实例和类型化 CE。PARAM 必须被 SDK 观察为受支持、可设置的 DOUBLE 数组，旧数值完整、有限且长度匹配。字段读取未知或条件不满足时保留证据并阻止写入。

只有 `status=prepared` 才返回可用于执行的 `scoped_revision`。它绑定请求数值位模式、完整旧数值、规定身份、owner、模块和上下文，不是数据库锁或完整元件快照。NAME、单位元数据或可选引用的普通读取失败可以保留 partial 原始证据；它们不抹掉已经完整观察的必需数值切片。native fault 仍会立即终止整个操作。

`dry_run` 默认 true。预览禁止携带任何执行字段，即使值为 null。输入不接受 CE、名称、通配符、布尔值、字符串数值、非有限值、按索引补丁、数组扩缩或单位转换。

## 写入并核对

执行需要 MCP 进程开启 `E3D_MCP_ALLOW_COMMANDS=true`，并提交本次预览的 session、build、revision，以及调用方分配的新请求 UUID：

```json
{
  "name": "e3d_paragon_component_parameters_set",
  "arguments": {
    "refno": "=2/3",
    "parameters": [0.0, 80.0, 125.5],
    "dry_run": false,
    "request_id": "00000000-0000-0000-0000-000000000002",
    "expected_session_id": "<来自本次参数预览>",
    "expected_bridge_build": "<来自本次参数预览的 UUID>",
    "expected_revision": "<来自本次参数预览的 64 位小写摘要>"
  }
}
```

执行在命令门禁内占有唯一 UI 回调，重新核对必需证据和 revision，再通过 SDK 的 `SetAttribute(DbAttribute, double[])` 写入。接口不切换 CE、不执行 PML。成功必须由独立回读证明：目标身份、owner 和规定上下文保持一致，完整 PARAM 的长度、顺序和每个 DOUBLE 位模式都与请求相符。

`status=updated` 表示 setter 返回且必需回读通过。`status=unchanged` 表示当前数值已相同，未调用 setter，并完成独立回读。setter 返回、SDK 可设置标志或账本完成阶段，单独都不能证明操作成功。普通 setter 异常即使随后读到目标数值，也不会升级为成功。

数值按原生 DOUBLE 处理。接口不推导每个参数的尺寸意义、单位或有效工程范围；`units_verified=false`、`units_converted=false`，几何和工程验证保持未知。单位元数据的普通失败不影响已完整读取的数值比较。

## 超时和失败

参数请求使用独立的托管账本：

```json
{
  "name": "e3d_paragon_component_parameters_status",
  "arguments": {
    "request_id": "00000000-0000-0000-0000-000000000002",
    "expected_session_id": "<原参数请求的 session_id>"
  }
}
```

查询不访问 SDK，也不检查忙状态或故障状态；已保存的历史故障仍可查询。重复 ID 不重新执行。账本最多保留 64 条回执，完成回执最多保留 30 分钟，会话内最多记住 65536 个已用 ID；完整回执超过 256 KiB 时仍保留关键事实。账本不跨会话持久化，未知或过期不证明此前未写入。

检查 `progress` 和账本 `critical_summary` 中的目标 REF、写入尝试、setter 返回、参数比较、回读、上下文、故障及不确定状态。失败后可能已有参数写入；不要自动换 ID 重试。接口不回滚、恢复 CE、删除元件或 SAVEWORK。

复制和参数写入是两个独立操作。只有复制回执已验证 `known_new_refno` 时，才应将其准确传给参数工具；本工具本身不证明该目标刚被创建。参数写入失败时，保留原复制回执和新 REF，分别检查两个请求，不把参数失败当作需要重新复制的依据。

## DOUBLE 位模式和 HTTP

MCP 输入只接受数值数组，不接受 `parameter_bits`。Python 将输入转换为有限 binary64，并在 HTTP 请求中自动附上有序的 16 位小写十六进制位模式。原生端校对数值与位模式后重建 DOUBLE，回执的 request、before、after 分别保留其位模式。

这个额外字段用于保留负零：net48 的 JSON 数值解析及输出会把 `-0.0` 变成正零。HTTP 校验只对零允许数值符号丢失；非零值必须与位模式精确一致，非有限位模式始终拒绝。回读数值的正负零以显式位模式为准，不从 JSON 的零符号推断。

- `POST /api/v1/paragon/component-parameters`：最多 8192 原始 UTF-8 字节，无 query，严格 JSON；HTTP 必需 `refno`、`parameters`、`parameter_bits`，其余字段与 MCP 对应。
- `GET /api/v1/paragon/component-parameters/status`：只接受唯一的 `request_id`、`expected_session_id`，无 body。
- 所有响应绑定服务器 session 与 Addin MVID。不支持的 Addin 返回 404；错误方法返回带 `Allow` 的 405。不会转发到通用属性写入或 PML。
- `/health.api_features` 仅在实现 optional interface 时声明 `paragon_component_parameters` 和 `paragon_component_parameters_status`。
