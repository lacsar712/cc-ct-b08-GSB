# 数控刀补复核台

操作员提交刀具编号与刀补微米值；后台 worker 用 PostgreSQL 行锁（`select_for_update(skip_locked=True)`）认领待复核记录，按绝对值是否不超过 12 微米给出「合格」或「超差」。

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
| machinist | machine123456 | 可提交刀补 |
| auditor | audit123456 | 只读列表 |

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

## 冻结归档台（只读归档包）

顶部导航「冻结归档台」。操作员可开一个归档包（打开时即冻结台）：

- **左栏「排队待签」**：把刀补记录加入排队；每条可「签发」或「移出」。
- **右栏「已签 · 包内栏目」**：点签发当刻，把**刀号 / 刀补 / 判定 / 理由**四样冻进包，定格在签发那一刻；此后该栏目只读。
- **封包**：待签清空后可封包，整包转为只读归档包，禁止再增改、禁止删除、禁止改名/解封。
- 右栏每条同时展示「包内（冻结）」与「现行（总览）」两行，可直接**对拍**：总览现行数字随后续判定可改，包内三字段始终停在签发当刻；出现差异时高亮标 ⚠。
- **权限**：machinist（操作员）可开包/入队/签发/封包；auditor（复核员，观察账号）可翻包查看，但没有任何签发/变更按钮，写接口一律 403。

写口保护是双层的，不止前端：

1. 接口层不提供任何改包内字段的 PATCH/PUT；重复签发、改已封包均被拒（409）。
2. 模型/查询集层拦截 ORM `save()`、`save(update_fields=...)`、`QuerySet.update/bulk_update/delete`、实例 `delete()`；已签栏目不可改、不可删、不可回退状态；来源刀补记录被 `PROTECT`，不能通过删源记录级联清掉包内栏目。

### 归档验收脚本

无 Docker 时可用 SQLite 内存库跑（仅测试用；生产仍是 PostgreSQL，`config/settings_sqlite.py` 只把库切到内存 SQLite）：

```bash
python3 -m venv --without-pip .venv
curl -sS https://bootstrap.pypa.io/get-pip.py | .venv/bin/python
.venv/bin/pip install -r backend/requirements.txt
cd backend
DJANGO_SETTINGS_MODULE=config.settings_sqlite ../.venv/bin/python manage.py test desk
```

覆盖：签两笔 → 封包 → auditor 403 → 人为改库/重算一行刀补 → 重开包内仍停在签发当刻读数、总览已变、对拍有差 → 各类写口均被拒。

## 目录

```text
backend/          Django 工程（config/、desk/、worker.py）
frontend/         SolidJS 单页
docker-compose.yml
PRD.md
```
