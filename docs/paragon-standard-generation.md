# 在 PARAGON 中按规范生成 SCOM

`e3d_paragon_generate_from_standard` 在已配置的 Catalogue 中生成一个 SCOM。两条路径都基于已有叶子模板，并在同一 CATE 下创建新元件。使用 E3D21 PARAGON 和支持相应接口的 Addin。

## E3D 项目规范

在项目中选定 `SPEC` 和其下的 `SPCO`。工具从 `SPCO` 向上核实归属，并通过 `CATREF` 找到实际 Catalogue SCOM。将该模板所属的 CATE 作为 `parent_refno`。以下 REF 和名称为示例。

```json
{
  "basis": {
    "kind": "e3d_specification",
    "spec_refno": "=7/20",
    "spco_refno": "=7/25"
  },
  "parent_refno": "=7/100",
  "name": "/EXAMPLE-SCOM",
  "dry_run": true
}
```

此路径复制模板及其参数。原 SPCO 继续引用原模板。将新元件纳入规范属于独立的项目操作。

## 国标、ASME 或企业标准

按项目使用的版次和适用条件选择条款或表格行，并核对模板中每个 PARAM 位置的含义及单位。将来源数据和完整映射交给工具。可填写 `document_sha256` 以记录所用文件的指纹，工具会随回执保留这个值。

以下使用假想尺寸和位置，示范两项 PARAM 的输入结构。

```json
{
  "basis": {
    "kind": "external_standard",
    "standard": "PROJECT-EXAMPLE",
    "edition": "2026",
    "locator": "表 4，第 3 行",
    "source_refno": "=7/101",
    "mapping": [
      {"parameter_index": 1, "quantity": "外径", "source_value": 4, "source_unit": "in", "param_unit": "mm"},
      {"parameter_index": 2, "quantity": "长度", "source_value": 150, "source_unit": "mm", "param_unit": "mm"}
    ]
  },
  "parent_refno": "=7/100",
  "name": "/EXAMPLE-SCOM",
  "dry_run": true
}
```

索引从 1 开始，每个位置恰好映射一次，数组长度与模板一致，上限为 128。支持 `mm` 和 `in` 之间换算，无量纲使用 `1`。预览回执的 `parameters` 显示转换后的完整数组。工具依据调用方提供的条款、数据和 PARAM 含义进行生成，标准选用和生成后的工程适用性由项目审核。

## 执行并验收

收到 `status: prepared` 后，以相同依据、父 CATE 和名称提交 `dry_run: false`。使用预览中的 `session_id`、`bridge_build` 和 `result.scoped_revision` 填写 `expected_session_id`、`expected_bridge_build` 和 `expected_revision`。为复制提供新的 `request_id`。外部规范还需要独立的 `parameter_request_id`。执行要求 MCP 开启 `E3D_MCP_ALLOW_COMMANDS=true`。

执行前会再次核对预览所依赖的证据。成功回执返回 `result.known_new_refno`。外部规范路径还会核对新元件的身份及上下文，并读回完整参数数组。复制与参数写入各有自己的请求记录。若出现 `partial`，请检查 `result.error.stage`、已保存的回执和新 REF。复制请求可用 `copy_status_args` 查询，参数请求可用 `parameters_status_args` 查询。用原请求 ID 核对结果后再决定如何处理。

[模板复制与状态查询](paragon-component-generation.md) · [参数写入与状态查询](paragon-component-parameters.md)
