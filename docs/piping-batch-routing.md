# 批量管道链接与路径优化

`e3d_piping_batch_route_plan` 一次接收多组管道头尾 P-point，按原生 WORLD 位置和 PDIR 方向计算候选中心线，并在每条路线的候选中排序。最多读取 16 个 P-point、规划 8 条路线；同一设备上的多个 P-point 会合并成一次原生读取。规划过程只读，不创建 PIPE/BRAN，也不连接头尾。

## 输入示例

`head_endpoint_index` 和 `tail_endpoint_index` 是 `endpoints` 中从 0 开始的索引。每个 P-point 在一次批次中只能分配到一条路线；多个路线 ID 必须唯一。公共弯曲半径、最短直管长度和容差为必填项，可在单条路线中覆盖。`lead_length_mm` 可选；省略时使用“弯曲半径 + 最短直管长度”作为两端的切线引出长度，并在结果里回显来源。

```json
{
  "endpoints": [
    {"refno": "=1/410", "expected_type": "NOZZ", "point_number": 1},
    {"refno": "=1/520", "expected_type": "NOZZ", "point_number": 2},
    {"refno": "=1/630", "expected_type": "NOZZ", "point_number": 1},
    {"refno": "=1/740", "expected_type": "NOZZ", "point_number": 1}
  ],
  "routes": [
    {"route_id": "P-101", "head_endpoint_index": 0, "tail_endpoint_index": 1},
    {"route_id": "P-102", "head_endpoint_index": 2, "tail_endpoint_index": 3,
     "bend_radius_mm": 125}
  ],
  "bend_radius_mm": 100,
  "minimum_straight_mm": 50,
  "position_tolerance_mm": 0.01,
  "direction_tolerance_degrees": 1
}
```

工具按组件分组读取所选连接点，逐个校验类型、REF、P-point 编号和 E3D 会话上下文，然后搜索直接路径和 WORLD XYZ 六种轴顺序路径，并尝试两端 PDIR 的正反方向。候选按中心线长度、弯头数量、折线长度排序，返回每条路线最多五个候选。批级 beam search 最多保留 64 个组合状态，先减少路线输入折线相交/接触，再比较总长度、弯头数和折线长度。90°弯曲角度才会保留，以符合当前原生落模工具支持的固定 90° ELBO 模板。

`optimization_complete` 表示所有路线都找到了几何候选；`complete` 和 `success` 只表示本次原生端点读取完整且上下文一致。若原生读取完整但找不到几何候选，路线状态会指出原因，不能据此推断数据库读取失败。

## 结果范围

批内组合会优先减少所选输入折线之间的相交或接触，再比较路线长度、弯头数和折线长度。`batch_interactions` 只检查所选 WORLD 输入折线，不计算圆弧、管道外径、支吊架、周边模型或净距；接触提示不是碰撞判定。

候选集仅包括直接路径和有限的轴顺序组合，因此“最优”是所搜索候选中的排序结果，不是全空间搜索或工程最优证明。工具不验证规格、口径、目录可用性、压力等级或设计标准，也不判断设备喷嘴是否允许连接。每次调用都重新读取端点；跨请求不是数据库锁，读过程也不是原子快照。

## 后续落模

选中路线的 `native_route_create_geometry` 会把端点索引重映射成两端版本，并输出中心线参数，便于后续单条落模请求复用。它只是 geometry draft，缺少目标 ZONE、名称、模板 BRAN/ELBO，也没有原生 revision。通过 `e3d_piping_route_create` 预览时，原生创建工具会重新核对端点、规格、模板半径、弯头几何和 TUBI 读回。批量规划不会自动执行这些单条创建，也不会把现有端点连接到新 BRAN；创建后 HREF/TREF 仍保持未连接状态。
