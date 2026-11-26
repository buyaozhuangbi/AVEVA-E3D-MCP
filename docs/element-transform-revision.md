# 变换 revision 保护

`e3d_set_transform` 现在按“预览一次、执行一次”工作。

先用 `dry_run=true` 提交目标 REF、位置/方向和 `wrt`。E3D21 原生回执中的 `result.revision` 是 64 位小写十六进制值，绑定当前会话、工程/MDB、数据库/extract 身份、CE、目标和 OWNER、坐标系、现有位姿以及本次请求。

执行时必须用完全相同的参数并传入 `expected_revision`，同时使用 `dry_run=false` 和命令权限。服务只调用专用的 `POST /api/v1/element/transform/revisioned`；旧版 bridge 返回 404 时不会改走没有 revision 保护的旧接口。预览可以重复读取，执行 token 失效时会在任何 setter、回滚或 `Command.Update` 之前拒绝。

revision 是 compare-and-set 的请求快照，不是数据库锁、事务或工程适用性判断。成功写入后仍需检查 `write_applied`、`readback_verified`、`notification_update` 和 CE/坐标系核验；通知失败不会自动重复写入。

MDB 可以同时挂载多套 Design 数据库。revision 会读取目标元素实际所属的数据库/extract，并把该数据库的 WORLD 身份纳入快照；它不会把 MDB 顺序中的第一套 Design WORLD 当成所有目标的公共锚点。这样在目标位于后续 Design 数据库、而另一套数据库的 WORLD 暂时不可读时，目标变换仍按自己的数据库身份进行校验。

WORLD 识别使用 WORL/WORLD 类型短名、原始 REF 的元素分量 0 和数据库原生 WORLD 身份。`DbElementType.IsWorld` 在本机 E3D 2.1 上不能作为这一类型判定，创建预检和变换框架验证也不依赖它。
