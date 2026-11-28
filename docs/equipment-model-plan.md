# 设备建模计划与空间提示

`e3d_equipment_model_plan` 为一个明确的 EQUI 及其已有成员规划 create、copy、transform 步骤。它读取完整有界的设备清单、父级/目标 Schema、已请求的属性约束、WORLD 位姿和坐标转换，并用前后上下文快照检查项目、MDB、session、bridge build 与当前 CE。计划只读；可选的原生预览只请求各写入工具的 `dry_run`，不会执行模型写入。

当一个计划包含至少两个不同的 transform 目标时，`spatial_review` 会尝试为独立正 BOX/CYLI 读取原生尺寸和当前 WORLD 位姿，并结合计划位姿计算解析包围盒。它比较目标的轴对齐包围盒；BOX/BOX 包围盒重叠时再用 OBB 分离轴检查区分真实有向盒重叠候选和纯 AABB 假阳性。它报告 `overlap_candidate`、`contact_candidate`、`aabb_overlap_only`、`separate` 或 `unknown`。重复变换同一目标时不会推测步骤间的位姿组合；不支持的类型、负几何、带成员的 primitive、上下文/位姿不完整或原生几何读取失败均会保留为未知。

此提示是保守的包围盒筛查，不是 E3D 原生 clash、精确实体相交或净距分析；OBB 分离只能排除 BOX/BOX 的部分 AABB 假阳性，CYLI 与混合形状仍只按 AABB 比较。它不改变设备步骤的 `status`、revision 或 write viability，不能代替工程检查。各 native read 是分开的观察，不构成数据库锁或原子快照；执行前仍须重新预览并使用原生 revision。
