# PARAGON Catalogue 数据库建库准备

`e3d_database_preflight` 生成只读建库候选。对 `database_type=CATA`，结果还会包含 `admin_handoff` 和 `post_create_verification`。它不会进入 ADMIN、发送 `CREATE DB`、创建根元素或修改 MDB。

## 在 ADMIN 中创建

示例输入为：

```json
{
  "name": "e3d_database_preflight",
  "arguments": {
    "team": "CATALOGUE",
    "name": "FITTINGS",
    "database_type": "CATA",
    "access": "UPDATE"
  }
}
```

该调用的 `candidate_command` 保持为非执行候选，`admin_handoff.suggested_command_template` 会附加 `WITH CATA /<CATALOGUE_ROOT_NAME>`。用户在 ADMIN 表单中选定根名后，再按实际项目配置完成建库。

先检查 preflight 的 `complete`、当前 MDB 名称和数据库冲突。`name_in_current_mdb=false` 仅表示完整扫描时当前 MDB 未观察到同名项，不代表项目范围内唯一。团队是否存在、目录是否可用、权限和项目级 DBNO 唯一性仍需在 ADMIN 中确认。

打开 E3D ADMIN 的 **Database > Create**，按 `admin_handoff` 填写：

- Owning Team 使用指定 TEAM，Database Name 使用指定名称，Database Type 选 `CATA`。
- Element Type 选 `CATA`，Element Name 由用户选择。该步骤会创建 Catalogue 根。
- Access Mode 选 `UPDATE` 或 `MULTIWRITE`。MULTIWRITE 的候选语法附带 `CLAIM IMPLICIT`。
- Area 和 DB Number 按交接包填写。省略 DBNO 时由 ADMIN 选择，不能把 preflight 的冲突检查当作号码分配锁。
- Controlled、Reference Only、Protected、Description、主存储位置和其他组织配置由 ADMIN 用户检查并填写。

`suggested_command_template` 对照本机安装的 `PMLLIB/admin/forms/admdb.pmlfrm` CREATE 分支构造。`<CATALOGUE_ROOT_NAME>` 是占位符，必须由用户替换。模板不代表已通过 PML 解析，也不能直接提交给 `e3d_execute_pml`。ADMIN 表单还会检查团队、区域、DBNO 和存储位置。工具没有替用户完成这些检查。

当前 E3D 2.10 bridge 工程没有引用独立的 AVEVA Administration 程序集。ADMIN 数据库 API 属于另一套运行时，因此本轮把建库交接给 ADMIN 表单，没有把候选命令接到通用命令接口，也没有声称已验证当前用户的 ADMIN 权限。

ADMIN 创建的 CATA 数据库及根元素与后续 PARAGON 元件生成是分开的步骤。根下面的 SECT、CATE、SCOM 等目录层级仍需按项目配置建立。新库若未出现在目标 MDB，应由用户在 ADMIN 中将它加入该 MDB，再重新连接/读取。

## 建库后核验

建库并完成 MDB 配置后，使用 `post_create_verification.steps` 中的只读工具调用：

1. `e3d_database_state` 从当前 MDB 读取新库名称、类型和数据库号。若首个 200 项页面有后续页，使用返回的 `next_offset` 和 `context_id` 继续。
2. `e3d_team_database_identity_manifest` 使用这次新鲜读取的 `context_id`，核对 typed DB → DBLI → TEAM owner chain 及当前 MDB 匹配。
3. `e3d_paragon_catalogue_manifest` 使用 `=<observed_database_number>/0`，确认 element 0 是 Catalogue 数据库中的原生根，并把观察到的根名与 `post_create_verification.expected_root_name` 核对。

三项是分开的 SDK 读取，不构成数据库锁或一个原子快照。完整核验要求它们观察到相同 session、project 和 MDB；部分或截断的读取保留为未知。该流程只能证明数据库和根在当前 MDB 中可见，不能证明项目范围内唯一、Admin 权限、磁盘备份、未来可写或本次请求创建了该数据库。

如果已安装的 E3D Addin 无法连接，先恢复 bridge 连接；不要据此重发建库命令。当前实现没有建库执行、重试、回滚或删除接口。
