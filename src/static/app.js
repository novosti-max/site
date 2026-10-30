(() => {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const fmt = n => (n == null || n === "" ? "—" : Number(n).toLocaleString("ru-RU"));
  const plural = (n, a, b, c) => { n = Math.abs(n) % 100; if (n > 10 && n < 15) return c; n %= 10; return n === 1 ? a : n > 1 && n < 5 ? b : c; };
  const KEY = "nm-pick";

  let catalog = {};
  let order = [];
  const pick = new Set();

  // ---------- storage ----------
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify([...pick])); } catch (e) {} };
  const restore = () => {
    const url = new URL(location.href);
    const fromUrl = url.searchParams.get("pick");
    if (fromUrl) {
      fromUrl.split(",").forEach(id => id && pick.add(id));
      url.searchParams.delete("pick");
      history.replaceState(null, "", url.pathname + (url.search || "") + url.hash);
      return true;
    }
    try { (JSON.parse(localStorage.getItem(KEY) || "[]")).forEach(id => pick.add(id)); } catch (e) {}
    return false;
  };

  // ---------- toast ----------
  const toastEl = $("[data-toast]");
  let toastT;
  const toast = msg => {
    if (!toastEl) return;
    toastEl.textContent = msg;
    toastEl.classList.add("show");
    clearTimeout(toastT);
    toastT = setTimeout(() => toastEl.classList.remove("show"), 2200);
  };

  // ---------- selection ----------
  const add = id => { if (catalog[id]) pick.add(id); };
  const sync = () => {
    $$("[data-add]").forEach(b => {
      const on = pick.has(b.dataset.add);
      b.setAttribute("aria-pressed", on);
      b.setAttribute("aria-label", (on ? "Убрать из подборки: " : "Добавить в подборку: ") + (catalog[b.dataset.add]?.name || ""));
      b.closest("tr")?.classList.toggle("on", on);
    });
    const ids = [...pick].filter(id => catalog[id]);
    const subs = ids.reduce((s, id) => s + (catalog[id].subs || 0), 0);
    const withViews = ids.filter(id => catalog[id].views != null);
    const views = withViews.reduce((s, id) => s + catalog[id].views, 0);

    $$("[data-pick-count]").forEach(e => (e.textContent = ids.length));
    $$("[data-pick-word]").forEach(e => (e.textContent = plural(ids.length, "канал", "канала", "каналов")));
    $$("[data-pick-subs]").forEach(e => (e.textContent = fmt(subs)));
    $$("[data-pick-views]").forEach(e => (e.textContent = withViews.length ? fmt(views) : "—"));
    $$("[data-pick-views-row]").forEach(e => (e.hidden = !withViews.length));
    $$(".pick-btn").forEach(b => b.classList.toggle("has", ids.length > 0));

    const bar = $("[data-pickbar]");
    if (bar) bar.hidden = ids.length === 0;
    $("[data-pick-empty]").hidden = ids.length > 0;
    $("[data-pick-sum]").hidden = ids.length === 0;

    const list = $("[data-pick-list]");
    list.innerHTML = "";
    ids.map(id => catalog[id]).sort((a, b) => b.subs - a.subs).forEach(c => {
      const li = document.createElement("li");
      li.innerHTML = `<span class="nm"></span><span class="sb"></span><button type="button" aria-label="Убрать">×</button>`;
      li.querySelector(".nm").textContent = c.name;
      const rg = document.createElement("span");
      rg.className = "rg";
      rg.textContent = c.region;
      li.querySelector(".nm").appendChild(rg);
      li.querySelector(".sb").textContent = fmt(c.subs);
      li.querySelector("button").addEventListener("click", () => { pick.delete(c.id); save(); sync(); });
      list.appendChild(li);
    });
    updateRequestText();
  };

  // ---------- drawer ----------
  const drawer = $("[data-drawer]");
  const scrim = $("[data-scrim]");
  let lastFocus;
  const openDrawer = () => {
    lastFocus = document.activeElement;
    drawer.hidden = false; scrim.hidden = false;
    $("[data-close-pick]").focus();
  };
  const closeDrawer = () => {
    drawer.hidden = true; scrim.hidden = true;
    lastFocus?.focus?.();
  };

  // ---------- request ----------
  const dlg = $("#request");
  const reqText = () => {
    const ids = [...pick].filter(id => catalog[id]).sort((a, b) => catalog[b].subs - catalog[a].subs);
    const f = dlg ? new FormData(dlg.querySelector("form")) : new FormData();
    const project = (f.get("project") || "").trim();
    const date = (f.get("date") || "").trim();
    const comment = (f.get("comment") || "").trim();
    const subs = ids.reduce((s, id) => s + catalog[id].subs, 0);
    const lines = ["Здравствуйте! Хочу разместить рекламу в сети «Новости в MAX»."];
    if (project) lines.push(`Проект: ${project}`);
    if (date) lines.push(`Желаемая дата: ${date}`);
    if (comment) lines.push(`Комментарий: ${comment}`);
    lines.push("", `Каналы (${ids.length}, ${fmt(subs)} подписок суммарно):`);
    ids.forEach((id, i) => lines.push(`${i + 1}. ${catalog[id].name} — ${catalog[id].region}, ${fmt(catalog[id].subs)}`));
    lines.push("", "Подборка: " + shareLink(), "Подскажите стоимость и свободные даты.");
    return lines.join("\n");
  };
  const shareLink = () => `${location.origin}/?pick=${[...pick].join(",")}#catalog`;
  const updateRequestText = () => { const t = $("[data-req-text]"); if (t) t.value = reqText(); };
  const copy = async (text, ok) => {
    try { await navigator.clipboard.writeText(text); toast(ok); }
    catch (e) {
      const ta = document.createElement("textarea");
      ta.value = text; document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); toast(ok); } catch (e2) { toast("Не удалось скопировать: выделите текст вручную"); }
      ta.remove();
    }
  };
  const csv = () => {
    const ids = [...pick].filter(id => catalog[id]);
    const rows = [["Канал", "Регион", "Тип", "Подписчики", "Просмотры за 24 ч", "Ссылка"]]
      .concat(ids.map(id => { const c = catalog[id]; return [c.name, c.region, {radar: "Радар", city: "Городские новости", media: "Медиа"}[c.type], c.subs, c.views ?? "", c.max_url]; }));
    const body = rows.map(r => r.map(v => `"${String(v).replace(/"/g, '""')}"`).join(";")).join("\r\n");
    const blob = new Blob(["﻿" + body], {type: "text/csv;charset=utf-8"});
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "podborka-novosti-max.csv";
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  };

  // ---------- catalog filters ----------
  const cat = $("#cat");
  const state = {type: "all", region: "", q: "", sort: "subs", all: false};
  const LIMIT = 25;
  const applyFilters = () => {
    if (!cat) return;
    const tbody = cat.tBodies[0];
    const rows = $$("tr.ch", tbody);
    const q = state.q.toLowerCase().replace(/ё/g, "е");
    let shown = 0;
    rows.forEach(r => {
      const ok = (state.type === "all" || r.dataset.type === state.type)
        && (!state.region || r.dataset.region === state.region)
        && (!q || r.dataset.name.replace(/ё/g, "е").includes(q));
      r.hidden = !ok;
      if (ok) shown++;
    });
    const key = state.sort;
    rows.sort((a, b) => key === "name"
      ? a.dataset.name.localeCompare(b.dataset.name, "ru")
      : (Number(b.dataset[key]) || 0) - (Number(a.dataset[key]) || 0));
    rows.forEach(r => tbody.appendChild(r));
    // без фильтров показываем первые 25, остальные — по кнопке
    const collapse = !state.all && state.type === "all" && !state.region && !q && shown > LIMIT;
    if (collapse) rows.filter(r => !r.hidden).slice(LIMIT).forEach(r => (r.hidden = true));
    const more = $("[data-more]");
    if (more) more.hidden = !collapse;
    $("[data-shown]").textContent = collapse ? LIMIT : shown;
    $("[data-empty]").hidden = shown > 0;
    cat.closest(".tbl-wrap").hidden = shown === 0;
  };

  // ---------- events ----------
  document.addEventListener("click", e => {
    const t = e.target.closest("button, a");
    if (!t) return;
    if (t.dataset.add) { pick.has(t.dataset.add) ? pick.delete(t.dataset.add) : add(t.dataset.add); save(); sync(); return; }
    if (t.dataset.addIds !== undefined) {
      t.dataset.addIds.split(",").forEach(add); save(); sync();
      toast("Добавлено в подборку"); openDrawer(); return;
    }
    if (t.hasAttribute("data-add-all")) { Object.keys(catalog).forEach(add); save(); sync(); toast("Вся сеть в подборке"); openDrawer(); return; }
    if (t.hasAttribute("data-add-shown")) {
      $$("tr.ch:not([hidden])", cat).forEach(r => add(r.dataset.id)); save(); sync();
      toast("Показанные каналы добавлены"); return;
    }
    if (t.hasAttribute("data-more")) { state.all = true; applyFilters(); return; }
    if (t.hasAttribute("data-open-pick")) { openDrawer(); return; }
    if (t.hasAttribute("data-close-pick")) { closeDrawer(); return; }
    if (t.hasAttribute("data-clear-pick")) { pick.clear(); save(); sync(); return; }
    if (t.hasAttribute("data-open-request")) { closeDrawer(); updateRequestText(); dlg.showModal(); return; }
    if (t.hasAttribute("data-copy-req")) { copy(reqText(), "Заявка скопирована. Отправьте её в MAX"); return; }
    if (t.hasAttribute("data-copy-link")) { copy(shareLink(), "Ссылка на подборку скопирована"); return; }
    if (t.hasAttribute("data-csv")) { csv(); return; }
    if (t.dataset.typeFilter) {
      state.type = t.dataset.typeFilter;
      $$("[data-type-filter]").forEach(b => b.setAttribute("aria-checked", b === t));
      applyFilters(); return;
    }
  });
  scrim?.addEventListener("click", closeDrawer);
  document.addEventListener("keydown", e => { if (e.key === "Escape" && !drawer.hidden) closeDrawer(); });
  dlg?.addEventListener("input", updateRequestText);

  const regSel = $("[data-region-filter]");
  regSel?.addEventListener("change", () => { state.region = regSel.value; applyFilters(); });
  $("[data-search]")?.addEventListener("input", e => { state.q = e.target.value.trim(); applyFilters(); });
  $("[data-sort]")?.addEventListener("change", e => { state.sort = e.target.value; applyFilters(); });

  // ---------- init ----------
  fetch("/assets/catalog.json")
    .then(r => r.json())
    .then(data => {
      data.channels.forEach(c => { catalog[c.id] = c; });
      order = data.channels.map(c => c.id);
      const shared = restore();
      const reg = new URL(location.href).searchParams.get("region");
      if (reg && regSel && [...regSel.options].some(o => o.value === reg)) { regSel.value = reg; state.region = reg; applyFilters(); }
      applyFilters();
      sync();
      if (shared && pick.size) { save(); openDrawer(); toast("Открыта подборка по ссылке"); }
    })
    .catch(() => {});
})();
