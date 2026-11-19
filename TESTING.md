# 实际验证记录

运行环境：Python 3.11 / SQLAlchemy(SQLite 独立测试库) / NumPy 2.2 / SciPy 1.15 / pytest。
Docker 与 PostgreSQL/Redis 服务在本环境不可用，因此以下验证在本地临时 venv 内以 SQLite 执行；
Redis 队列与限流中间件在测试中用替身（stub），生产路径仍使用 compose 中的 Redis7 与 PostgreSQL16。

```bash
cd backend
DATABASE_URL="sqlite+pysqlite:///:memory:" REDIS_URL="redis://127.0.0.1:1/0" JWT_SECRET=test PYTHONPATH=. pytest tests -q
```

结果：`18 passed`。

## 覆盖内容

| 场景 | 验证方式 | 结果 |
|---|---|---|
| 名册锁定：每班纸样数必须偶数、名称/编号不重复、锁定后重抽返回 409 | API 测试 | 通过 |
| 每班 AB/BA 等额分派，席位与配额随 seed 可复现 | `allocate` 单测 + API 测试 | 通过 |
| 并发领单：8 线程同时领取同一席位，分派顺序一致、仅 1 次成功领取、重试不改顺序 | 线程池 + API 测试 | 通过 |
| 错序登记：实际顺序与锁定分派不符直接 422，不进入排队 | API 测试 | 通过 |
| 读数登记幂等；重复不同读数返回 409，开测后不补抽 | API 测试 | 通过 |
| 缺测纸样仍占原名册席位；<2 对完整读数拒绝排队 | API 测试 | 通过 |
| 排队时冻结名册与读数：生成 SHA256，之后补录/改值不进入已冻结快照 | API 测试 | 通过 |
| 按席位精确枚举：40 席仅 16 完整 + 24 缺测仍用精确权重（Fraction），p 与解析值一致 | 单测 | 通过 |
| 独立 oracle（直接穷举每班全部 `C(seats, quota)` 席位分派）核对精确 p，含非均匀配对差、多班次、混合缺测 | 单测 | 通过 |
| 交换两次读数：平均差变号、双侧 p 不变 | 单测 | 通过 |
| 逐张自由翻转的 p 与按班次等额重排的 p 不同；验证不使用实际不可能的顺序（例：8 完整对 2 班，p=2/C(4,2)² 而非 2/C(8,4)） | 单测 | 通过 |
| Monte Carlo 路径：固定 seed 可复现，加一校正，超几何抽取与席位配额一致 | 单测 | 通过 |
| 导出 CSV 含「检验依据」：设计、统计量、重排规则、班次配额、假设 | API 测试 | 通过 |
| 演示种子：锁定 2 班×8 席，每班留 1 席缺测，快照计算得 16 席/14 完整/2 缺测 | 种子脚本 | 通过 |
| 角色权限：viewer 不能锁定/领单/登记读数，可读名册；analyst 可写 | API 测试 | 通过 |
| 前端 ES modules 语法检查（`node --check`） | 语法检查 | 通过 |

## 未在本环境执行

- `docker compose up --build` 全栈联调（本环境无 Docker）。
- PostgreSQL 16、Redis 7 真实连接与 worker 心跳 healthcheck。
- 浏览器端到端点击流程（仅做模块语法检查与 API 级测试）。

上述未执行项在具备 Docker 的环境应按 README「快速启动」与「一天的工作剧本」补做。
