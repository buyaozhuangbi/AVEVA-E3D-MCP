# 原生元素祖先与数据库身份

`e3d_element_ancestry` 用一个只读 E3D21 回调检查一个明确数字 REF、其 OWNER 链、Design WORLD 和当前 MDB 的数据库目录。它不使用当前 CE 或名称查找，不执行 PML，不改变模型、视图或 CE。

```json
{
  "refno": "=2013286676/490",
  "max_depth": 32
}
```

`refno` 必须是规范 ASCII 数字形式 `=positiveInt32/nonnegativeInt32`，不接受空格、前导零、名称或超出 Int32 的数字。`max_depth` 为 1–64，默认 32，计数包含目标本身；目标在 `ancestors[0]`，深度字段为 `0`。达到上限仍未遇到 WORLD 时，结果标记 `truncated`。

每个元素分开返回 SDK 的 `raw_ref` / `raw_refno` 与格式化 `formatted_refno`。它们是不同来源的观察值，不能互相替代，也不能用 REF 推算数据库 Number。数据库身份从 SDK `DbElement.Db` 与 `MDB.GetDBArray()` 读取；数据库 Number 在该 SDK 中经当前 MDB 查找，因此同一 Number 对应多个 Extract 时，单个元素的精确 Extract 归属保持未知，并附带诊断。

`is_world` 保留 SDK `DbElementType.IsWorld` 的原始观察值，不用它判定 OWNER 链终点。本机 E3D 2.1 中已观察到 SITE 返回 `true`、WORL 返回 `false`。终点必须同时满足类型短名为 WORL/WORLD、原始 REF 的元素分量为 0、数据库为当前 MDB 的明确 Design 成员，并且 REF 与该数据库原生 WORLD 的 REF 一致。

先检查 `success`、`complete`、`reached_world`、`truncated` 和 `mdb_membership_complete`，再使用身份字段。MDB 扫描有上限 10000 项；不完整、重复或不可读的身份不当作“肯定不属于 MDB”，而是返回 `null` 和 `read_errors`。`context` 给出项目、MDB 和 CE 的前后观察及一致性结果；单次 UI 回调不是数据库锁，也不保证内容在调用结束后仍未变化。

不支持 E3D21 bridge 的运行时会显式报告接口不可用；mock 模式不会伪造原生数据库身份。详见工具 schema 与每条原生回执中的 `scope`、`database_identity_source` 和 `read_errors`。
