# 数控刀补复核台

操作员提交刀具编号与刀补微米值；后台 worker 用 PostgreSQL 行锁（`select_for_update(skip_locked=True)`）认领待复核记录，按绝对值是否不超过 12 微米给出「合格」或「超差」。

已结清（已完成）的刀补可在「冻结台」一点签发，打成只读归档包：刀号、刀补、判定、理由四字段冻进包内，此后复核总览的现行数字允许跟着新判定变化，包内字段永远停在签发那一版。

## 冻结台 / 只读归档包

- 导航「冻结台」：左边「排队待签」（已结清未签发的刀补），右边「已签条目」「归档包」与「包内栏目」。
- 操作员（machinist）点「签发」即把当刻读数冻进新归档包；观察账号（auditor）可以翻包，但看不到也调不通签发（403）。
- 打开归档包可见「对拍」：包内冻结值 vs 总览现行值，漂移行高亮。
- 只读是三层强制，不只是前端隐藏：
  1. API 只暴露签发（POST）与查询（GET），归档路由没有任何更新/删除端点；
  2. ORM 层拦截：`save`（非新增）/`delete`/`update`/`bulk_update` 一律抛 `ArchiveReadOnlyError`；
  3. 数据库层拦截：PostgreSQL 触发器 `BEFORE UPDATE OR DELETE` 直接报错，直连 SQL 也改不动。

## 技术栈

| 层 | 选型 |
|----|------|
| 后端 | Django 5 + django-ninja（ASGI / uvicorn） |
| 前端 | SolidJS + Vite，nginx 反代 `/api` |
| 数据库 | PostgreSQL 16 |
| 鉴权 | JWT（python-jose），令牌存浏览器 localStorage |

## 端口

| 服务 | 地址 |
|------|------|
| 页面 | http://localhost:3196 |
| 接口 | http://localhost:8196 |
| PostgreSQL | localhost:54396（库名 `cncoffset`） |

## 账号

| 用户 | 密码 | 权限 |
|------|------|------|
| machinist | machine123456 | 可提交刀补、签发归档包 |
| auditor | audit123456 | 只读列表与归档包，不能签发 |

## 启动

```bash
cd projects/17-cnc-tool-offset-desk
docker compose up --build
```

健康检查：`GET http://localhost:8196/api/health` → `{"status":"ok"}`

## 验收

1. machinist 登录后，种子数据应显示刀具 T01 合格（刀补 5 µm）、T09 超差（刀补 20 µm）。
2. 提交一条新刀补后，状态先为「待复核」，数秒内 worker 处理为「已完成」并给出结论。
3. auditor 登录后只能看列表，没有提交表单。
4. 冻结台：machinist 对 T01、T09 各点一次「签发」，生成两个只读归档包；随后人为改库（如 `UPDATE desk_offsetsubmission SET offset_um=99 WHERE tool_code='T01'`）或让 worker 重算其中一行，重开归档包，包内仍是签发当刻读数，「现行」列显示新值并标记「已漂移」。
5. 归档表任何改写都会被拒：API 无写口（PUT/PATCH/DELETE 一律 405）、ORM 抛 `ArchiveReadOnlyError`、直连 SQL 被触发器报错；auditor 调 `POST /api/archive/sign` 返回 403。

## 接口一览（归档）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/archive/queue` | 排队待签（已结清未签发） |
| GET | `/api/archive/entries` | 已签条目（冻结快照） |
| GET | `/api/archive/packages` | 归档包列表 |
| GET | `/api/archive/packages/{id}` | 包内栏目 + 现行对拍 |
| POST | `/api/archive/sign` | 签发（仅 machinist），body：`{"submission_id": 1, "reason": "可空"}` |

## 目录

```text
backend/          Django 工程（config/、desk/、worker.py）
frontend/         SolidJS 单页
docker-compose.yml
PRD.md
```
