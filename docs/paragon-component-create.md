# 在 PARAGON 中直接新建 SCOM

`e3d_paragon_component_create` 在现有 Catalogue CATE 下通过 E3D21 SDK 新建一个 SCOM，并设置名称、`PTREF`、`GMREF` 和完整数值 `PARAM`。点集和型集使用现有 PTSE 和 GMSE。多个 SCOM 可以指向同一套定义，因此后续编辑共享定义前应查看其引用关系。

## 预览并执行

打开 E3D21 的 PARAGON 模块，选定一个 CATE、一个 PTSE 和一个 GMSE，确认点和图元表达式所需的 PARAM 顺序及单位。以下 DBREF 和参数仅用于展示输入格式。

```json
{
  "parent_refno": "=7/100",
  "point_refno": "=7/201",
  "geometry_refno": "=7/202",
  "name": "/NEW-SCOM",
  "parameters": [80.0, 120.0],
  "dry_run": true
}
```

使用三个不同的数值 DBREF，并提供 1–128 个有限 DOUBLE 参数。预览验证 Catalogue 对象的基础与实际类型、父 CATE 的 SCOM 创建能力及名称占用情况。回执包含父级成员和每个集合的 1–64 个直接成员，含对象引用、类型与真实 owner。此范围用于验证成员身份与顺序。参数只写数值数组，参数名称及定义留在 Catalogue 中维护。点号、口径、方向、图元表达式和求值后的尺寸仍需用 E3D 实机核对。

收到 `status=prepared` 后，保留原参数，改为 `dry_run=false`，并添加以下执行字段：

| 字段 | 值 |
| --- | --- |
| `request_id` | 调用方生成的唯一小写 UUID |
| `expected_session_id` | 本次预览回执的 `session_id` |
| `expected_bridge_build` | 本次预览回执的 `bridge_build` |
| `expected_revision` | 本次预览回执的 `result.scoped_revision` |

执行要求 `E3D_MCP_ALLOW_COMMANDS=true`。写入前重新核对预览依据。创建后先确认 SDK 返回的是父 CATE 中唯一追加的新 SCOM，再写入名称、点集引用、型集引用和参数。成功回执为 `status=created`，用 `result.progress.known_new_refno` 取得新 REF。独立回读会验证名称、归属、两条引用及参数位模式。

## 查询原请求

创建和初始化分阶段执行。若发生错误，回执的 `progress` 会标明各阶段是否进入或返回，以及已知的新 REF 和验证状态。超时后可用 `e3d_paragon_component_create_status` 传入原 `request_id` 和 `expected_session_id`，读取当前桥接会话保存的账本。先检查原请求和已知新 REF，避免重复创建。保存工作由项目操作人员决定。

E3D21 Addin 通过 `/health.api_features` 声明 `paragon_component_create` 和 `paragon_component_create_status`。HTTP 使用 `POST /api/v1/paragon/component-create` 和 `GET /api/v1/paragon/component-create/status`。MCP 客户端直接传 `parameters`，Python 会补充 HTTP 所需的 binary64 位模式。
