import { createSignal, onMount, Show, For, createEffect } from "solid-js";
import {
  clearSession,
  createPackage,
  createSubmission,
  fetchPackage,
  fetchPackages,
  fetchSubmission,
  fetchSubmissions,
  getUser,
  login,
  queueEntry,
  removeEntry,
  sealPackage,
  setSession,
  signEntry,
} from "./api";

const statusLabel = {
  pending: "待复核",
  processing: "复核中",
  done: "已完成",
};

const roleLabel = {
  machinist: "操作员",
  auditor: "复核员",
};

const packageStatusLabel = {
  open: "冻结台 · 打开中",
  sealed: "只读归档包 · 已封",
};

function readHash() {
  const raw = (location.hash || "#/").replace(/^#/, "") || "/";
  let m = raw.match(/^\/detail\/(\d+)/);
  if (m) return { name: "detail", id: Number(m[1]) };
  m = raw.match(/^\/archive\/(\d+)/);
  if (m) return { name: "archiveDetail", id: Number(m[1]) };
  if (raw === "/archive") return { name: "archive", id: null };
  return { name: "home", id: null };
}

function App() {
  const [user, setUser] = createSignal(getUser());
  const [rows, setRows] = createSignal([]);
  const [detail, setDetail] = createSignal(null);
  const [route, setRoute] = createSignal(readHash());
  const [error, setError] = createSignal("");
  const [loading, setLoading] = createSignal(false);

  const [loginUser, setLoginUser] = createSignal("machinist");
  const [loginPass, setLoginPass] = createSignal("machine123456");

  const [toolCode, setToolCode] = createSignal("");
  const [offsetUm, setOffsetUm] = createSignal("");

  function goHome() {
    location.hash = "#/";
  }

  function goDetail(id) {
    location.hash = `#/detail/${id}`;
  }

  function goArchive() {
    location.hash = "#/archive";
  }

  async function loadRows() {
    setLoading(true);
    setError("");
    try {
      setRows(await fetchSubmissions());
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  async function loadDetail(id) {
    setLoading(true);
    setError("");
    try {
      setDetail(await fetchSubmission(id));
    } catch (e) {
      setError(e.message);
      setDetail(null);
    } finally {
      setLoading(false);
    }
  }

  onMount(() => {
    const onHash = () => setRoute(readHash());
    window.addEventListener("hashchange", onHash);
    if (user()) {
      if (route().name === "detail") loadDetail(route().id);
      else if (route().name === "home") loadRows();
    }
    return () => window.removeEventListener("hashchange", onHash);
  });

  createEffect(() => {
    const r = route();
    if (!user()) return;
    if (r.name === "detail" && r.id) loadDetail(r.id);
    if (r.name === "home") loadRows();
  });

  async function handleLogin(e) {
    e.preventDefault();
    setError("");
    try {
      const data = await login(loginUser(), loginPass());
      setSession(data.token, {
        username: data.username,
        role: data.role,
        can_write: data.can_write,
      });
      setUser(getUser());
      goHome();
      await loadRows();
    } catch (err) {
      setError(err.message);
    }
  }

  function handleLogout() {
    clearSession();
    setUser(null);
    setRows([]);
    setDetail(null);
    goHome();
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    try {
      await createSubmission(toolCode(), offsetUm());
      setToolCode("");
      setOffsetUm("");
      await loadRows();
    } catch (err) {
      setError(err.message);
    }
  }

  const isOverview = () => route().name === "home" || route().name === "detail";
  const isArchive = () => route().name === "archive" || route().name === "archiveDetail";

  return (
    <div class="page">
      <header class="topbar">
        <div class="brand">
          <h1>数控刀补复核台</h1>
          <p class="hint">刀补绝对值不超过十二微米判合格，否则超差。后台认领进程用行锁跳过已占行领取待复核。</p>
        </div>
        <Show when={user()}>
          <nav class="topnav">
            <a
              href="#/"
              class={isOverview() ? "active" : ""}
              onClick={(e) => {
                e.preventDefault();
                goHome();
              }}
            >
              复核总览
            </a>
            <a
              href="#/archive"
              class={isArchive() ? "active" : ""}
              onClick={(e) => {
                e.preventDefault();
                goArchive();
              }}
            >
              冻结归档台
            </a>
          </nav>
        </Show>
      </header>

      <Show when={error()}>
        <div class="banner error">{error()}</div>
      </Show>

      <Show
        when={user()}
        fallback={
          <section class="card">
            <h2>登录</h2>
            <form onSubmit={handleLogin} class="form">
              <label>
                用户名
                <input
                  value={loginUser()}
                  onInput={(e) => setLoginUser(e.currentTarget.value)}
                />
              </label>
              <label>
                密码
                <input
                  type="password"
                  value={loginPass()}
                  onInput={(e) => setLoginPass(e.currentTarget.value)}
                />
              </label>
              <button type="submit">进入系统</button>
            </form>
            <p class="hint">操作员 machinist / machine123456；复核员 auditor / audit123456（只读）</p>
          </section>
        }
      >
        <section class="card toolbar">
          <div>
            当前用户：<strong>{user().username}</strong>（{roleLabel[user().role] || user().role}
            {user().can_write ? "" : " · 观察账号，可翻包但不能签发"}）
          </div>
          <button type="button" class="ghost" onClick={handleLogout}>
            退出
          </button>
        </section>

        <Show when={route().name === "home"}>
          <Show when={user().can_write}>
            <section class="card">
              <h2>提交刀补</h2>
              <form onSubmit={handleSubmit} class="form inline">
                <label>
                  刀具编号
                  <input
                    placeholder="如 T01"
                    value={toolCode()}
                    onInput={(e) => setToolCode(e.currentTarget.value)}
                    required
                  />
                </label>
                <label>
                  刀补（微米）
                  <input
                    type="number"
                    value={offsetUm()}
                    onInput={(e) => setOffsetUm(e.currentTarget.value)}
                    required
                  />
                </label>
                <button type="submit">提交待复核</button>
              </form>
            </section>
          </Show>

          <section class="card">
            <div class="toolbar">
              <h2>复核列表（现行值）</h2>
              <button type="button" class="ghost" onClick={loadRows} disabled={loading()}>
                {loading() ? "刷新中…" : "刷新"}
              </button>
            </div>
            <table>
              <thead>
                <tr>
                  <th>刀具</th>
                  <th>刀补 µm</th>
                  <th>状态</th>
                  <th>结论</th>
                  <th>提交时间</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                <For each={rows()}>
                  {(row) => (
                    <tr>
                      <td>{row.tool_code}</td>
                      <td>{row.offset_um}</td>
                      <td>{statusLabel[row.status] || row.status}</td>
                      <td class={row.verdict === "合格" ? "pass" : row.verdict === "超差" ? "fail" : ""}>
                        {row.verdict || "—"}
                      </td>
                      <td>{new Date(row.created_at).toLocaleString()}</td>
                      <td>
                        <button type="button" class="ghost" onClick={() => goDetail(row.id)}>
                          详情
                        </button>
                      </td>
                    </tr>
                  )}
                </For>
              </tbody>
            </table>
            <Show when={!rows().length && !loading()}>
              <p class="hint">暂无记录</p>
            </Show>
          </section>
        </Show>

        <Show when={route().name === "detail"}>
          <section class="card">
            <div class="toolbar">
              <h2>刀补详情</h2>
              <button type="button" class="ghost" onClick={goHome}>
                返回总览
              </button>
            </div>
            <Show when={detail()} fallback={<p class="hint">{loading() ? "加载中…" : "未找到记录"}</p>}>
              {(d) => (
                <div class="detail-grid">
                  <p>编号：{d().id}</p>
                  <p>刀具：{d().tool_code}</p>
                  <p>刀补 µm：{d().offset_um}</p>
                  <p>状态：{statusLabel[d().status] || d().status}</p>
                  <p class={d().verdict === "合格" ? "pass" : d().verdict === "超差" ? "fail" : ""}>
                    结论：{d().verdict || "—"}
                  </p>
                  <p>提交时间：{new Date(d().created_at).toLocaleString()}</p>
                  <p>
                    复核时间：
                    {d().reviewed_at ? new Date(d().reviewed_at).toLocaleString() : "—"}
                  </p>
                </div>
              )}
            </Show>
          </section>
        </Show>

        <Show when={route().name === "archive"}>
          <ArchiveList user={user()} />
        </Show>
        <Show when={route().name === "archiveDetail"}>
          <PackageDesk user={user()} packageId={route().id} />
        </Show>
      </Show>
    </div>
  );
}

function verdictClass(v) {
  return v === "合格" ? "pass" : v === "超差" ? "fail" : "";
}

// 归档包列表：观察账号也可翻阅。
function ArchiveList(props) {
  const [packages, setPackages] = createSignal([]);
  const [name, setName] = createSignal("");
  const [busy, setBusy] = createSignal(false);
  const [err, setErr] = createSignal("");

  async function load() {
    setErr("");
    try {
      setPackages(await fetchPackages());
    } catch (e) {
      setErr(e.message);
    }
  }

  async function handleCreate(e) {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      const pkg = await createPackage(name().trim());
      setName("");
      location.hash = `#/archive/${pkg.id}`;
    } catch (ex) {
      setErr(ex.message);
    } finally {
      setBusy(false);
    }
  }

  onMount(load);

  return (
    <section class="card">
      <div class="toolbar">
        <h2>冻结归档台 · 归档包</h2>
        <button type="button" class="ghost" onClick={load}>
          刷新
        </button>
      </div>
      <Show when={err()}>
        <div class="banner error">{err()}</div>
      </Show>

      <Show when={props.user.can_write}>
        <form onSubmit={handleCreate} class="form inline">
          <label>
            新建归档包（打开为冻结台，封包后只读）
            <input
              placeholder="如 2026-09 夜班批次"
              value={name()}
              onInput={(e) => setName(e.currentTarget.value)}
              required
            />
          </label>
          <button type="submit" disabled={busy()}>
            开冻结台
          </button>
        </form>
      </Show>

      <table>
        <thead>
          <tr>
            <th>归档包</th>
            <th>状态</th>
            <th>排队待签</th>
            <th>已签栏目</th>
            <th>建立时间</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <For each={packages()}>
            {(p) => (
              <tr>
                <td>{p.name}</td>
                <td>
                  <span class={p.status === "sealed" ? "sealed-badge" : "open-badge"}>
                    {packageStatusLabel[p.status] || p.status}
                  </span>
                </td>
                <td>{p.queued_count}</td>
                <td>{p.signed_count}</td>
                <td>{new Date(p.created_at).toLocaleString()}</td>
                <td>
                  <button
                    type="button"
                    class="ghost"
                    onClick={() => (location.hash = `#/archive/${p.id}`)}
                  >
                    翻包
                  </button>
                </td>
              </tr>
            )}
          </For>
        </tbody>
      </table>
      <Show when={!packages().length}>
        <p class="hint">暂无归档包</p>
      </Show>
    </section>
  );
}

// 双栏冻结台：左排队待签（可签发），右已签包内只读栏目（含与现行值对拍）。
function PackageDesk(props) {
  const [pkg, setPkg] = createSignal(null);
  const [submissions, setSubmissions] = createSignal([]);
  const [pickId, setPickId] = createSignal("");
  const [busy, setBusy] = createSignal(false);
  const [err, setErr] = createSignal("");

  const canWrite = () => props.user.can_write;
  const sealed = () => pkg()?.status === "sealed";

  async function load() {
    setErr("");
    try {
      const [p, subs] = await Promise.all([fetchPackage(props.packageId), fetchSubmissions()]);
      setPkg(p);
      setSubmissions(subs);
    } catch (e) {
      setErr(e.message);
      setPkg(null);
    }
  }

  async function guard(fn) {
    setBusy(true);
    setErr("");
    try {
      await fn();
      await load();
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  const queued = () => (pkg()?.entries || []).filter((e) => e.state === "queued");
  const signed = () => (pkg()?.entries || []).filter((e) => e.state === "signed");
  const inPackageIds = () => new Set((pkg()?.entries || []).map((e) => e.submission_id));
  const available = () => submissions().filter((s) => !inPackageIds().has(s.id));

  onMount(load);

  return (
    <section class="card">
      <div class="toolbar">
        <h2>
          归档包：{pkg()?.name || "…"}{" "}
          <span class={sealed() ? "sealed-badge" : "open-badge"}>
            {pkg() ? packageStatusLabel[pkg().status] : ""}
          </span>
        </h2>
        <button type="button" class="ghost" onClick={() => (location.hash = "#/archive")}>
          返回归档包列表
        </button>
      </div>
      <Show when={err()}>
        <div class="banner error">{err()}</div>
      </Show>

      <Show when={canWrite() && !sealed()}>
        <div class="toolbar seal-row">
          <span class="hint">签发会把刀号 / 刀补 / 判定 / 理由当场冻进包，之后只读。</span>
          <button
            type="button"
            onClick={() => guard(() => sealPackage(pkg().id))}
            disabled={busy()}
          >
            封包（转只读归档）
          </button>
        </div>
      </Show>
      <Show when={sealed()}>
        <p class="hint sealed-note">本包已封，包内栏目为只读归档；下方“现行”列若与包内不一致即为后来新判定所致。</p>
      </Show>

      <div class="desk-cols">
        {/* 左：排队待签 */}
        <div class="desk-col">
          <h3 class="col-head">排队待签（{queued().length}）</h3>
          <For each={queued()}>
            {(e) => {
              const s = submissions().find((x) => x.id === e.submission_id);
              return (
                <div class="queue-item">
                  <div>
                    <strong>{s?.tool_code}</strong>　刀补 {s?.offset_um}µm
                    <span class={verdictClass(s?.verdict)}>{s?.verdict || "待判定"}</span>
                  </div>
                  <Show when={canWrite() && !sealed()}>
                    <div class="row-actions">
                      <button
                        type="button"
                        onClick={() => guard(() => signEntry(e.id))}
                        disabled={busy()}
                      >
                        签发
                      </button>
                      <button
                        type="button"
                        class="ghost"
                        onClick={() => guard(() => removeEntry(e.id))}
                        disabled={busy()}
                      >
                        移出
                      </button>
                    </div>
                  </Show>
                </div>
              );
            }}
          </For>
          <Show when={!queued().length}>
            <p class="hint">队列为空</p>
          </Show>

          <Show when={canWrite() && !sealed()}>
            <form
              class="queue-add"
              onSubmit={(ev) => {
                ev.preventDefault();
                if (!pickId()) return;
                guard(() => queueEntry(pkg().id, Number(pickId()))).then(() => setPickId(""));
              }}
            >
              <label>
                加入待签
                <select value={pickId()} onChange={(ev) => setPickId(ev.currentTarget.value)}>
                  <option value="">选择刀补记录…</option>
                  <For each={available()}>
                    {(s) => (
                      <option value={s.id}>
                        #{s.id} {s.tool_code} / {s.offset_um}µm / {s.verdict || "待判定"}
                      </option>
                    )}
                  </For>
                </select>
              </label>
              <button type="submit" disabled={busy() || !pickId()}>
                加入
              </button>
            </form>
          </Show>
        </div>

        {/* 右：已签包内栏目（只读，含对拍） */}
        <div class="desk-col signed-col">
          <h3 class="col-head">已签 · 包内栏目（只读，{signed().length}）</h3>
          <For each={signed()}>
            {(e) => (
              <div class={"signed-item" + (e.drift ? " drift" : "")}>
                <table class="snap-table">
                  <thead>
                    <tr>
                      <th></th>
                      <th>刀号</th>
                      <th>刀补 µm</th>
                      <th>判定</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr>
                      <td class="rowtag">包内（冻结）</td>
                      <td>{e.snap_tool_code}</td>
                      <td>{e.snap_offset_um}</td>
                      <td class={verdictClass(e.snap_verdict)}>{e.snap_verdict}</td>
                    </tr>
                    <tr class={e.drift ? "live-drift" : ""}>
                      <td class="rowtag">现行（总览）</td>
                      <td>
                        {e.live_tool_code}
                        <Show when={e.snap_tool_code !== e.live_tool_code}> ⚠</Show>
                      </td>
                      <td>
                        {e.live_offset_um}
                        <Show when={e.snap_offset_um !== e.live_offset_um}> ⚠</Show>
                      </td>
                      <td class={verdictClass(e.live_verdict)}>
                        {e.live_verdict}
                        <Show when={e.snap_verdict !== e.live_verdict}> ⚠</Show>
                      </td>
                    </tr>
                  </tbody>
                </table>
                <p class="reason">理由（随签发冻结）：{e.snap_reason}</p>
                <p class="hint">
                  签发时刻：{e.signed_at ? new Date(e.signed_at).toLocaleString() : "—"}
                  <Show when={e.drift}>
                    {" "}· <span class="fail">对拍有差：总览已随新判定改变，包内定格未变</span>
                  </Show>
                </p>
              </div>
            )}
          </For>
          <Show when={!signed().length}>
            <p class="hint">尚无已签栏目</p>
          </Show>
        </div>
      </div>
    </section>
  );
}

export default App;
