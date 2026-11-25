# 管道中心线与弯头空间规划

`e3d_piping_route_plan` 的可选 `centerline` 把两个原生 P-point 和调用方提供的 WORLD 路径点连接成直段与圆弧弯头。它复用本次计划已读取的端点，核对 session/build/project/MDB/CE 绑定、WORLD 坐标系、PPEXST、毫米 PPOS 和单位 PDIR。

这项能力在 Python MCP 侧计算，使用已有 Addin 的连接点读取接口，无需部署新的 DLL。公开工具仍为 122 个，版本维持 0.2.2。

## 调用示例

以下 REF 是输入格式示例，使用时替换为当前工程中明确存在的 PIPE/BRAN 和两个连接点。路径点必须与真实端点处于同一 WORLD 毫米坐标系。先用 `e3d_connection_points` 观察端点的位置和方向，再确定路径点与参数。

```json
{
  "root_refno": "=1/1",
  "planned_members": [],
  "endpoints": [
    {"refno": "=1/9", "expected_type": "NOZZ", "point_number": 4},
    {"refno": "=1/8", "expected_type": "NOZZ", "point_number": 1}
  ],
  "centerline": {
    "start_endpoint_index": 0,
    "end_endpoint_index": 1,
    "waypoints_world_mm": [[1000, 0, 0]],
    "bend_radius_mm": 100,
    "minimum_straight_mm": 50,
    "position_tolerance_mm": 0.01,
    "direction_tolerance_degrees": 0.1,
    "start_direction_sense": 1,
    "end_direction_sense": -1
  }
}
```

`start_endpoint_index`、`end_endpoint_index` 是 `endpoints` 数组中的不同零基索引。`waypoints_world_mm` 是按行进顺序给出的折线交点；空数组表示直连。最多支持 32 个中间点，坐标各分量限制在 ±1,000,000,000 mm 内。

`bend_radius_mm` 是调用方选定的中心线圆弧半径，范围 0.000001–1,000,000 mm；它不从口径推导，也不证明目录中存在对应弯头。所有转弯使用同一半径。`minimum_straight_mm` 对修剪后的每个真实直段生效，范围 0–1,000,000 mm；显式设为 0 时允许两个弯头切点相接，不生成零长度直管。

方向始终按从起点到终点行进解释：`start_direction_sense` 和 `end_direction_sense` 各自为 `1`（行进切向与该端点原生 PDIR 同向）或 `-1`（反向）。终点参数描述到达方向，不自动假定喷嘴朝向或流向。`direction_tolerance_degrees` 必填，范围 0–90°。

`position_tolerance_mm` 必填，范围 0.000001–1000 mm，用于识别重合或过短的相邻路径点。它不会放宽弯头空间不足或允许负长度直管。数值运算另外使用与 WORLD 坐标精度相关的舍入阈值；仅在该阈值内合并共线点或省略接近零的段。

## 读取结果

`result.centerline_plan` 保留输入选择、实际端点身份与坐标，并返回：

| 字段 | 含义 |
| --- | --- |
| `available` | 两端存在可绑定的 WORLD 毫米坐标。 |
| `complete` | 两端位置、方向和上下文证据可用；不代表几何能容纳。 |
| `geometry_fits` | 局部折线、弯头切向长度及最小直段约束是否满足。 |
| `geometric_fit` | 局部空间约束和两端方向是否都满足。缺失方向时保留 `null`，已确定的失败可为 `false`。 |
| `legs` | 合并共线点后的每条折线边长、相邻弯头所需切向长度、剩余直段和最小直段。 |
| `bend_candidates` | 每个转弯的交点、半径、转角、切向长度、切点、圆心、圆弧法向及局部径向轴。 |
| `elements` | 空间约束满足时按行进顺序排列的直段和圆弧。空间不足时为空；端点方向单独检查。 |
| `polyline_length_mm` / `centerline_length_mm` | 原始折线路径长与修剪后直段加圆弧的总长。无法组装时后者为 `null`。 |
| `diagnostics` | 重合/短段、折返、切向长度重叠、最小直段不足和端点方向不符的具体证据。 |

转角 θ 为相邻行进方向之间的夹角；圆弧在每条相邻折线边上的切向长度为 `R × tan(θ/2)`，圆弧长度为 `R × θ`。同一边两端的弯头切向长度必须共同计入。`bend_candidates` 中的 `radial_x_world`、`radial_y_world` 和 `normal_world` 定义解析圆弧坐标系，不是 E3D ELBO 的原生 POS/ORI。

当端点为 `[0,0,0]`、`[1000,1000,0]`，中间交点为 `[1000,0,0]`，半径为 100 mm 且方向匹配时，得到两个 900 mm 直段和一个 90°、长约 157.080 mm 的圆弧，总长约 1957.080 mm。

## 与建模计划的关系

省略 `centerline` 时保留原有调用和输出。只有指定 `centerline` 时，`planned_members` 才能为空；同时指定成员时，成员创建预览仍使用调用方显式提供的属性和位姿。中心线结果不会自动填充、修改或认证这些成员，`planned_member_geometry_verified`、`catalogue_verified`、`execution_prepared` 均为 `false`。仅规划路径时没有可准备的原生编辑，`native_previews.status` 为 `not_applicable`（请求预览时）。

整体 `result.complete` 继续覆盖全部请求的原生观察。隐式 TUBI 的某些连接字段不受 SDK 支持时，整体计划可能不完整，但两端独立核验通过的中心线仍能计算。检查完整结构化回执中的 `centerline_plan`；MCP 的 `isError` 可能反映整体计划的不完整。反过来，完整观察也可能得到 `geometric_fit=false`。

本次实机只读验证使用同一现有管道的两个 ELBO 端点和四个中间路径点：100 mm 半径得到 5 个直段、4 个弯头，总长约 2062.119 mm；400 mm 半径明确报告 `bend_takeouts_overlap`，不返回组装路径。核验前后的 session/project/MDB/CE 一致，未执行模型写入。

该计算覆盖局部中心线空间和端点切向，尚未检查路径自交、障碍物、外径包络、净距、规格或目录元件尺寸。落成原生管道仍需独立的目录选择、真实元素创建与连接操作及其原生回读。
