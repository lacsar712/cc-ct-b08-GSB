import { createSignal, onMount, Show, For, createEffect } from "solid-js";
import {
  clearSession,
  createSubmission,
  fetchArchiveEntries,
  fetchArchivePackage,
  fetchArchivePackages,
  fetchArchiveQueue,
  fetchSubmission,
  fetchSubmissions,
  getUser,
  login,
  setSession,
  signArchive,
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

function readHash() {
  const raw = (location.hash || "#/").replace(/^#/, "") || "/";
  const m = raw.match(/^\/detail\/(\d+)/);
  if (m) return { name: "detail", id: Number(m[1]) };
  if (raw === "/freeze") return { name: "freeze", id: null };
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

  const [queue, setQueue] = createSignal([]);
  const [signedEntries, setSignedEntries] = createSignal([]);
  const [packages, setPackages] = createSignal([]);
  const [openPackage, setOpenPackage] = createSignal(null);
  const [reasons, setReasons] = createSignal({});
  const [signingId, setSigningId] = createSignal(null);

  function goHome() {
    location.hash = "#/";
  }

  function goDetail(id) {
    location.hash = `#/detail/${id}`;
  }

  function goFreeze() {
    location.hash = "#/freeze";
  }

  async function loadRows() {
    setLoading(true);
    setError("");
    try {
      const data = await fetchSubmissions();
      setRows(data);
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

  async function loadFreeze() {
    setLoading(true);
    setError("");
    try {
      const [q, entries, pkgs] = await Promise.all([
        fetchArchiveQueue(),
        fetchArchiveEntries(),
        fetchArchivePackages(),
      ]);
      setQueue(q);
      setSignedEntries(entries);
      setPackages(pkgs);
      const current = openPackage();
      if (current) {
        const stillThere = pkgs.find((p) => p.id === current.id);
        if (stillThere) {
          setOpenPackage(await fetchArchivePackage(stillThere.id));
        } else {
          setOpenPackage(null);
        }
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  async function openPkg(id) {
    setError("");
    try {
      setOpenPackage(await fetchArchivePackage(id));
    } catch (e) {
      setError(e.message);
    }
  }

  async function handleSign(row) {
    setSigningId(row.id);
    setError("");
    try {
      const entry = await signArchive(row.id, (reasons()[row.id] || "").trim());
      setReasons((prev) => ({ ...prev, [row.id]: "" }));
      await loadFreeze();
      if (entry && entry.package_id) await openPkg(entry.package_id);
    } catch (err) {
      setError(err.message);
    } finally {
      setSigningId(null);
    }
  }

  onMount(() => {
    const onHash = () => setRoute(readHash());
    window.addEventListener("hashchange", onHash);
    if (user()) {
      if (route().name === "detail") loadDetail(route().id);
      else if (route().name === "freeze") loadFreeze();
      else loadRows();
    }
    return () => window.removeEventListener("hashchange", onHash);
  });

  createEffect(() => {
    const r = route();
    if (!user()) return;
    if (r.name === "detail" && r.id) loadDetail(r.id);
    if (r.name === "home") loadRows();
    if (r.name === "freeze") loadFreeze();
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
    setQueue([]);
    setSignedEntries([]);
    setPackages([]);
    setOpenPackage(null);
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
              class={route().name === "home" ? "active" : ""}
              onClick={(e) => {
                e.preventDefault();
                goHome();
              }}
            >
              复核总览
            </a>
            <a
              href="#/freeze"
              class={route().name === "freeze" ? "active" : ""}
              onClick={(e) => {
                e.preventDefault();
                goFreeze();
              }}
            >
              冻结台
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
            当前用户：<strong>{user().username}</strong>（{roleLabel[user().role] || user().role}）
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
              <h2>复核列表</h2>
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

        <Show when={route().name === "freeze"}>
          <div class="freeze-grid">
            <section class="card">
              <div class="toolbar">
                <h2>排队待签（已结清）</h2>
                <button type="button" class="ghost" onClick={loadFreeze} disabled={loading()}>
                  {loading() ? "刷新中…" : "刷新"}
                </button>
              </div>
              <table>
                <thead>
                  <tr>
                    <th>刀具</th>
                    <th>刀补 µm</th>
                    <th>结论</th>
                    <th>复核时间</th>
                    <th>签发</th>
                  </tr>
                </thead>
                <tbody>
                  <For each={queue()}>
                    {(row) => (
                      <tr>
                        <td>{row.tool_code}</td>
                        <td>{row.offset_um}</td>
                        <td class={row.verdict === "合格" ? "pass" : row.verdict === "超差" ? "fail" : ""}>
                          {row.verdict || "—"}
                        </td>
                        <td>{row.reviewed_at ? new Date(row.reviewed_at).toLocaleString() : "—"}</td>
                        <td>
                          <Show
                            when={user().can_write}
                            fallback={<span class="hint">只读</span>}
                          >
                            <div class="sign-cell">
                              <input
                                placeholder="理由（可空）"
                                value={reasons()[row.id] || ""}
                                onInput={(e) =>
                                  setReasons((prev) => ({
                                    ...prev,
                                    [row.id]: e.currentTarget.value,
                                  }))
                                }
                              />
                              <button
                                type="button"
                                disabled={signingId() === row.id}
                                onClick={() => handleSign(row)}
                              >
                                {signingId() === row.id ? "签发中…" : "签发"}
                              </button>
                            </div>
                          </Show>
                        </td>
                      </tr>
                    )}
                  </For>
                </tbody>
              </table>
              <Show when={!queue().length && !loading()}>
                <p class="hint">暂无待签发的结清刀补</p>
              </Show>
              <Show when={!user().can_write}>
                <p class="hint">观察账号只读：可以翻包，不能点签发。</p>
              </Show>
            </section>

            <div class="freeze-right">
              <section class="card">
                <h2>已签条目</h2>
                <table>
                  <thead>
                    <tr>
                      <th>包号</th>
                      <th>刀具</th>
                      <th>刀补 µm（冻）</th>
                      <th>结论（冻）</th>
                      <th>理由（冻）</th>
                      <th>签发人</th>
                      <th>签发时间</th>
                    </tr>
                  </thead>
                  <tbody>
                    <For each={signedEntries()}>
                      {(entry) => (
                        <tr>
                          <td>#{entry.package_id}</td>
                          <td>{entry.tool_code}</td>
                          <td>{entry.offset_um}</td>
                          <td class={entry.verdict === "合格" ? "pass" : "fail"}>{entry.verdict}</td>
                          <td>{entry.reason}</td>
                          <td>{entry.signed_by_name}</td>
                          <td>{new Date(entry.signed_at).toLocaleString()}</td>
                        </tr>
                      )}
                    </For>
                  </tbody>
                </table>
                <Show when={!signedEntries().length && !loading()}>
                  <p class="hint">暂无已签条目</p>
                </Show>
              </section>

              <section class="card">
                <h2>归档包</h2>
                <table>
                  <thead>
                    <tr>
                      <th>包号</th>
                      <th>标题</th>
                      <th>条目</th>
                      <th>创建人</th>
                      <th>创建时间</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    <For each={packages()}>
                      {(pkg) => (
                        <tr class={openPackage()?.id === pkg.id ? "pkg-open" : ""}>
                          <td>#{pkg.id}</td>
                          <td>{pkg.title}</td>
                          <td>{pkg.entry_count}</td>
                          <td>{pkg.created_by_name}</td>
                          <td>{new Date(pkg.created_at).toLocaleString()}</td>
                          <td>
                            <button type="button" class="ghost" onClick={() => openPkg(pkg.id)}>
                              打开
                            </button>
                          </td>
                        </tr>
                      )}
                    </For>
                  </tbody>
                </table>
                <Show when={!packages().length && !loading()}>
                  <p class="hint">暂无归档包</p>
                </Show>
              </section>

              <Show when={openPackage()}>
                {(pkg) => (
                  <section class="card">
                    <h2>
                      包内栏目 · #{pkg().id} {pkg().title}
                    </h2>
                    <p class="hint">
                      包内字段为签发当刻快照，任何写接口均不可改写；「现行」列取自复核总览实时数据，用于对拍。
                    </p>
                    <table>
                      <thead>
                        <tr>
                          <th>刀具（冻）</th>
                          <th>刀补 µm（冻）</th>
                          <th>结论（冻）</th>
                          <th>理由（冻）</th>
                          <th>现行刀补</th>
                          <th>现行结论</th>
                          <th>对拍</th>
                        </tr>
                      </thead>
                      <tbody>
                        <For each={pkg().entries}>
                          {(entry) => (
                            <tr class={entry.drifted ? "drift" : ""}>
                              <td>{entry.tool_code}</td>
                              <td>{entry.offset_um}</td>
                              <td class={entry.verdict === "合格" ? "pass" : "fail"}>{entry.verdict}</td>
                              <td>{entry.reason}</td>
                              <td>{entry.current_offset_um ?? "—"}</td>
                              <td>{entry.current_verdict || "—"}</td>
                              <td>
                                <Show
                                  when={entry.current_status !== null}
                                  fallback={<span class="badge gone">现行已删</span>}
                                >
                                  <Show
                                    when={entry.drifted}
                                    fallback={<span class="badge same">一致</span>}
                                  >
                                    <span class="badge drift">已漂移</span>
                                  </Show>
                                </Show>
                              </td>
                            </tr>
                          )}
                        </For>
                      </tbody>
                    </table>
                  </section>
                )}
              </Show>
            </div>
          </div>
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
      </Show>
    </div>
  );
}

export default App;
