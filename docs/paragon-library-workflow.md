# PARAGON 元件库工作流

当前 MCP 可以准备 CATA 数据库建库信息、核验已存在的 Catalogue、复制或新建 SCOM，并单独更新数值 PARAM。Catalogue 根、SECT/CATE 层级以及 PTSE/GMSE 定义仍须先在目标工程中建立。此页按可验证顺序列出各阶段和写入边界。

## 1. 建立并核验 CATA 数据库

使用 [`e3d_database_preflight`](../README.md) 生成 ADMIN 表单交接，在 E3D ADMIN 中创建 CATA 数据库及根 CATA，再把数据库加入目标 MDB。预检不会执行数据库创建。

重新连接后，从 `e3d_database_state` 读取完整数据库清单和新鲜 `context_id`。TEAM 身份检查将 `limit` 设为 `250`；若清单仍分页，保留 partial 结果并读取后续页。然后以 ADMIN 填写的根 `/NAME` 调用 `e3d_paragon_catalogue_manifest` 和 `e3d_paragon_catalogue_schema`，核实根元素身份、Catalogue 存储类型、DBREF 与可用子类型。名称查找失败、读错误或上下文变化都不能当成“根不存在”或“建库成功”。

## 2. 建立 SECT 和 CATE 层级

`e3d_paragon_element_create_preview` 只对明确 Catalogue 父级读取 CATA、SECT、CATE 的原生 `MemberTypes` 和当前 `IsCreatable`。可分别预览 `CATA → SECT` 与 `SECT → CATE`，并结合 `e3d_paragon_catalogue_schema` 观察父级成员类型。

该工具是只读预览，不创建目录成员。通用 `e3d_create_element` 当前限定 Design 数据库，不能作为 CATA 写入器。请在 PARAGON 中按项目要求建立 SECT/CATE，随后重新读取 Catalogue manifest 和 schema。尚无经验证的 MCP SECT/CATE 执行器。

## 3. 准备 PTSE 和 GMSE 定义

在目标 CATE 中建立适用于项目的 PTSE 点集和 GMSE 几何集。现有 SCOM 创建器要求两个数值 REF 指向 Catalogue 中已有的非空 PTSE、GMSE；会检查各自最多 64 个直接成员的类型化身份、顺序和真实 owner。它不生成集合成员，也不求值点位、图元表达式、尺寸或单位。

修改共享点集或几何集前，先用 `e3d_paragon_component_template` 和 `e3d_paragon_reference_trace` 检查引用关系。多条 SCOM 可能复用同一集合，修改定义会影响所有引用者。

## 4. 创建并核对 SCOM

有完整且适用的集合后，可选择以下方式：

- `e3d_paragon_component_create`：在明确 CATE 下引用 PTSE/GMSE，并设置名称和完整数值 PARAM。
- `e3d_paragon_component_copy`：在同一 CATE 内复制已读取的 SCOM 模板。
- `e3d_paragon_generate_from_standard`：从 E3D `SPEC/SPCO` 或调用方提供的标准参数映射生成。
- `e3d_paragon_component_parameters_set`：对已存在的 SCOM 独立更新完整数值 PARAM 数组。

所有写工具先用默认 `dry_run=true`。只有 `status=prepared` 才能用同一回执中的 session、build 和 revision 执行；发生超时或错误时先查原 request ID 状态，不要换 ID 重试。成功回执只覆盖各工具明确核验的身份、归属、追加顺序、引用及数值；几何求值、工程适用性和 `SAVEWORK` 仍需由项目操作人员检查。

## 验收边界

实机工具只在确认有效的 Catalogue 根、目标数据库访问状态及 PARAGON 模块上下文后调用。离线 SDK 构建和合同测试不能替代目标 Catalogue 中的运行验收；没有已核实的可写目标和根时，不执行目录或元件写入。
