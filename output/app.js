// Thai Stock Screener — คัดหุ้นจากข้อมูลใน data/*.js ตามเกณฑ์ใน data/presets.js (สำเนาของ presets.json)
// ตรรกะผ่าน/ไม่ผ่านตรงกับ metrics.passes() ใน screener/metrics.py
(function () {
  "use strict";

  const DATA = window.SCREENER_DATA || {};
  const PRESETS = window.SCREENER_PRESETS;
  const SOURCES = ["yahoo", "setsmart"].filter((s) => DATA[s]);
  const OPS = { ">=": (v, x) => v >= x, "<=": (v, x) => v <= x, ">": (v, x) => v > x, "<": (v, x) => v < x };
  const OP_TEXT = { ">=": "≥", "<=": "≤", ">": ">", "<": "<" };

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // ---------- สถานะ ----------
  const fresh = () => ({
    minMcap: PRESETS.universe.min_mcap,
    presets: Object.fromEntries(PRESETS.presets.map((p) => [p.id, {
      gates: p.gates.map((g) => ({ ...g, on: true })),
      weights: p.score.map((s) => ({ ...s })),
    }])),
  });
  const state = {
    source: SOURCES[0],
    presetId: PRESETS.presets[0].id,
    showFailed: false,
    search: "",
    sort: { key: "score", dir: -1 },
    ...fresh(),
  };
  let charts = [];

  const ds = () => DATA[state.source];
  const defs = () => ds().meta.metrics;
  const presetDef = () => PRESETS.presets.find((p) => p.id === state.presetId);
  const cur = () => state.presets[state.presetId];

  // ---------- ตัวเลข ----------
  function fmt(v, unit) {
    if (v === null || v === undefined) return "–";
    const nd = unit === "ลบ." ? 0 : unit === "ปี" ? 0 : Math.abs(v) >= 100 ? 1 : 2;
    return v.toLocaleString("th-TH", { minimumFractionDigits: nd, maximumFractionDigits: nd });
  }
  const label = (k) => defs()[k].label;
  const unit = (k) => defs()[k].unit;
  const withUnit = (k, v) => fmt(v, unit(k)) + (unit(k) === "%" ? "%" : "");

  // ---------- คัด + ให้คะแนน ----------
  function evaluate(stock, gates) {
    const fails = [];
    for (const g of gates) {
      if (!g.on || (stock.fin && g.skip_fin)) continue;
      const v = stock.m[g.metric];
      if (v === null || v === undefined) fails.push(`ข้อมูลไม่พอ: ${label(g.metric)}`);
      else if (!OPS[g.op](v, g.value)) fails.push(`${label(g.metric)} ${withUnit(g.metric, v)} (เกณฑ์ ${OP_TEXT[g.op]} ${fmt(g.value, unit(g.metric))})`);
    }
    return fails;
  }

  // percentile 0–100 ภายในกลุ่มที่ผ่าน ค่าเท่ากันได้อันดับเฉลี่ย · ไม่มีค่า = 0
  function percentiles(rows, metric, better) {
    const vals = rows.map((r) => r.s.m[metric]).filter((v) => v !== null && v !== undefined);
    const sorted = [...vals].sort((a, b) => a - b);
    const n = sorted.length;
    return rows.map((r) => {
      const v = r.s.m[metric];
      if (v === null || v === undefined || n === 0) return 0;
      if (n === 1) return 100;
      let lo = 0, hi = 0;
      for (const x of sorted) { if (x < v) lo++; if (x <= v) hi++; }
      const p = ((lo + hi - 1) / 2) / (n - 1) * 100;
      return better === "low" ? 100 - p : p;
    });
  }

  function screen() {
    const { gates, weights } = cur();
    const q = state.search.trim().toLowerCase();
    const base = ds().stocks.filter((s) => (s.m.mcap || 0) >= state.minMcap);
    const rows = base.map((s) => ({ s, fails: evaluate(s, gates) }));
    const passed = rows.filter((r) => !r.fails.length);
    const wsum = weights.reduce((a, w) => a + w.weight, 0) || 1;
    const pcts = weights.map((w) => percentiles(passed, w.metric, w.better));
    passed.forEach((r, i) => {
      r.parts = weights.map((w, j) => ({ metric: w.metric, pct: pcts[j][i], w: w.weight / wsum }));
      r.score = r.parts.reduce((a, p) => a + p.pct * p.w, 0);
    });
    [...passed].sort((a, b) => b.score - a.score).forEach((r, i) => { r.rank = i + 1; });
    const shown = (state.showFailed ? rows : passed)
      .filter((r) => !q || r.s.t.toLowerCase().includes(q) || r.s.n.toLowerCase().includes(q));
    return { base: base.length, passed: passed.length, rows: sortRows(shown) };
  }

  function sortRows(rows) {
    const { key, dir } = state.sort;
    const val = (r) => key === "score" ? (r.score ?? -Infinity)
      : key === "t" ? r.s.t : (r.s.m[key] ?? null);
    return rows.sort((a, b) => {
      if (!a.fails.length !== !b.fails.length) return a.fails.length ? 1 : -1;  // ตัวที่ผ่านอยู่บนเสมอ
      const x = val(a), y = val(b);
      if (x === null) return 1;
      if (y === null) return -1;
      return (x < y ? -1 : x > y ? 1 : 0) * dir;
    });
  }

  // ---------- แผงเกณฑ์ ----------
  function rangeCtl(id, labelHtml, value, min, max, step, opts = {}) {
    return `<div class="ctl${opts.off ? " off" : ""}" data-id="${id}">
      <div class="ctl-head">
        ${opts.check !== undefined ? `<input type="checkbox" data-role="on" ${opts.check ? "checked" : ""} aria-label="เปิดใช้เกณฑ์">` : ""}
        <label for="n-${id}">${labelHtml}</label>
        <span class="val">${opts.prefix || ""}<input type="number" id="n-${id}" data-role="num" value="${value}" min="${min}" max="${max}" step="${step}">${opts.suffix || ""}</span>
      </div>
      <input type="range" data-role="range" value="${value}" min="${min}" max="${max}" step="${step}" aria-label="${esc(opts.aria || "")}">
      ${opts.note ? `<div class="note">${opts.note}</div>` : ""}
    </div>`;
  }

  function renderPanel() {
    $("universe").innerHTML = rangeCtl("mcap", "Market cap ขั้นต่ำ", state.minMcap, 0, 20000, 500,
      { suffix: " ลบ.", aria: "Market cap ขั้นต่ำ" });
    $("gates").innerHTML = cur().gates.map((g, i) => {
      const u = unit(g.metric);
      return rangeCtl("g" + i, esc(label(g.metric)), g.value, g.min, g.max, g.step, {
        check: g.on, off: !g.on, prefix: OP_TEXT[g.op] + " ", suffix: u === "%" ? "%" : " " + u,
        aria: label(g.metric), note: g.skip_fin ? "ไม่ใช้กับธนาคาร/การเงิน" : "",
      });
    }).join("");
    renderWeights();
  }

  function renderWeights() {
    const ws = cur().weights;
    const sum = ws.reduce((a, w) => a + w.weight, 0) || 1;
    $("weights").innerHTML = ws.map((w, i) => rangeCtl("w" + i,
      `${esc(label(w.metric))} <span class="na">(${w.better === "low" ? "ต่ำดี" : "สูงดี"})</span>`,
      w.weight, 0, 100, 5, { suffix: ` = ${Math.round(w.weight / sum * 100)}%`, aria: "น้ำหนัก " + label(w.metric) })).join("");
  }

  function onPanelInput(e) {
    const ctl = e.target.closest(".ctl");
    if (!ctl) return;
    const id = ctl.dataset.id, role = e.target.dataset.role;
    if (role === "on") {
      cur().gates[+id.slice(1)].on = e.target.checked;
      ctl.classList.toggle("off", !e.target.checked);
      return renderTable();
    }
    const v = parseFloat(e.target.value);
    if (Number.isNaN(v)) return;
    ctl.querySelector(role === "num" ? "[data-role=range]" : "[data-role=num]").value = v;
    if (id === "mcap") state.minMcap = v;
    else if (id[0] === "g") cur().gates[+id.slice(1)].value = v;
    else {
      cur().weights[+id.slice(1)].weight = v;
      if (e.type === "change") renderWeights();
      else updateWeightLabels();
    }
    renderTable();
  }

  function updateWeightLabels() {
    const ws = cur().weights;
    const sum = ws.reduce((a, w) => a + w.weight, 0) || 1;
    $("weights").querySelectorAll(".ctl").forEach((el, i) => {
      el.querySelector(".val").lastChild.textContent = ` = ${Math.round(ws[i].weight / sum * 100)}%`;
    });
  }

  // ---------- ตาราง ----------
  function columns() {
    const keys = [];
    for (const g of cur().gates) if (!keys.includes(g.metric)) keys.push(g.metric);
    for (const w of cur().weights) if (!keys.includes(w.metric)) keys.push(w.metric);
    keys.push("mcap");
    return keys;
  }

  function renderTable() {
    const { base, passed, rows } = screen();
    const cols = columns();
    const sortAttr = (k) => state.sort.key === k ? ` aria-sort="${state.sort.dir > 0 ? "ascending" : "descending"}"` : "";
    let html = `<thead><tr>
      <th class="l">#</th><th class="l sticky" data-sort="t"${sortAttr("t")}>Ticker</th><th class="l">ชื่อ</th>
      <th data-sort="score"${sortAttr("score")}>คะแนน</th>
      ${cols.map((k) => `<th data-sort="${k}"${sortAttr(k)} title="${esc(defs()[k].formula)}">${esc(label(k))}${unit(k) === "%" ? " %" : ""}</th>`).join("")}
      ${state.showFailed ? `<th class="l">ตกเกณฑ์</th>` : ""}
    </tr></thead><tbody>`;
    for (const r of rows) {
      const s = r.s, ok = !r.fails.length;
      const tip = ok ? r.parts.map((p) => `${label(p.metric)}: ${Math.round(p.pct)} × ${Math.round(p.w * 100)}%`).join("\n") : "";
      html += `<tr class="${ok ? "" : "failed"}">
        <td class="l">${ok ? r.rank : "–"}</td>
        <td class="l sticky"><button type="button" class="tk" data-detail="${esc(s.t)}">${esc(s.t)}</button>${s.fin ? ' <span class="chip fin" title="ธนาคาร/การเงิน">การเงิน</span>' : ""}</td>
        <td class="l name" title="${esc(s.n)}">${esc(s.n)}</td>
        <td>${ok ? `<span class="score" title="${esc(tip)}"><span class="bar"><i style="width:${r.score.toFixed(0)}%"></i></span>${r.score.toFixed(0)}</span>` : '<span class="na">–</span>'}</td>
        ${cols.map((k) => `<td>${numBtn(s, k)}</td>`).join("")}
        ${state.showFailed ? `<td class="l reasons">${r.fails.map((f) => `<span class="chip">✕ ${esc(f)}</span>`).join("")}</td>` : ""}
      </tr>`;
    }
    if (!rows.length) html += `<tr><td class="l" colspan="${cols.length + 5}">ไม่มีหุ้นที่ผ่านเกณฑ์ — ลองผ่อนเกณฑ์หรือเปิด "แสดงตัวที่ไม่ผ่าน"</td></tr>`;
    $("table").innerHTML = html + "</tbody>";
    $("summary").innerHTML = `ผ่าน <strong>${passed}</strong> จาก ${base} ตัว (market cap ≥ ${fmt(state.minMcap, "ลบ.")} ลบ.) · ${esc(presetDef().name)} · ${esc(ds().meta.label)}`;
  }

  function numBtn(s, k) {
    const v = s.m[k];
    if (v === null || v === undefined) return `<button type="button" class="num na" data-src="${esc(s.t)}|${k}" title="ไม่มีข้อมูล — กดดูเหตุผล">–</button>`;
    return `<button type="button" class="num" data-src="${esc(s.t)}|${k}">${withUnit(k, v)}</button>`;
  }

  // ---------- popover ที่มา ----------
  function sourceHtml(s, k) {
    const meta = ds().meta, d = defs()[k];
    const v = s.m[k];
    const items = [...new Set(d.inputs)].map((f) => {
      const src = meta.fields[f] || { name: f };
      const when = f === "price" ? `ราคาปิด ${meta.price_date}` : f === "shares" ? `ดึงเมื่อ ${meta.fetched}` : `${s.per}${src.how ? " · " + src.how : ""}`;
      const tpl = src.url || (meta.url && src.page ? meta.url.replace("{page}", src.page) : null);
      const url = tpl ? tpl.replace("{t}", encodeURIComponent(s.t)) : null;
      return `<li><code>${esc(src.name)}</code> <span class="muted">· ${esc(when)}</span>${url ? ` · <a href="${url}" target="_blank" rel="noopener">เปิดที่มา</a>` : ""}</li>`;
    }).join("");
    const where = meta.source === "yahoo"
      ? `Yahoo Finance · ดึงเมื่อ ${esc(meta.fetched)}`
      : esc(meta.file_note);
    return `<h3>${esc(s.t)} · ${esc(d.label)} = ${v === null || v === undefined ? "ไม่มีข้อมูล" : esc(withUnit(k, v)) + (d.unit === "%" ? "" : " " + esc(d.unit))}</h3>
      <p class="formula">${esc(d.formula)}</p>
      <ul>${items}</ul>
      <p class="muted">แหล่ง: ${where}${v === null || v === undefined ? " · คำนวณไม่ได้เพราะข้อมูลบางช่องว่าง หรือค่าติดลบ (ไม่เดาค่าแทน)" : ""}</p>`;
  }

  function showPopover(btn) {
    const [t, k] = btn.dataset.src.split("|");
    const s = ds().stocks.find((x) => x.t === t);
    const pop = $("popover");
    pop.innerHTML = sourceHtml(s, k);
    pop.hidden = false;
    const b = btn.getBoundingClientRect(), w = pop.offsetWidth, h = pop.offsetHeight;
    pop.style.left = Math.max(16, Math.min(b.left, innerWidth - w - 16)) + "px";
    pop.style.top = (b.bottom + h + 8 > innerHeight ? Math.max(8, b.top - h - 6) : b.bottom + 6) + "px";
  }

  // ---------- แผงรายตัว ----------
  function openDetail(t) {
    const s = ds().stocks.find((x) => x.t === t);
    if (!s) return;
    $("detail-title").textContent = `${s.t}`;
    const presetChips = PRESETS.presets.map((p) => {
      const fails = evaluate(s, state.presets[p.id].gates);
      return `<span class="chip ${fails.length ? "" : "ok"}" title="${esc(fails.join("\n"))}">${fails.length ? "✕" : "✓"} ${esc(p.name)}</span>`;
    }).join("");
    const grid = Object.keys(defs()).map((k) => `<div><small>${esc(label(k))}</small>${numBtn(s, k)}${unit(k) !== "%" && s.m[k] != null ? ` <span class="na">${esc(unit(k))}</span>` : ""}</div>`).join("");
    const h = s.h, years = Object.keys(s.dps);
    $("detail-body").innerHTML = `
      <p class="sub">${esc(s.n)}${s.sec ? " · " + esc(s.sec) : ""}${s.fin ? ' · <span class="chip fin">การเงิน</span>' : ""}<br>
        งวดล่าสุด ${esc(s.per)} · ${esc(ds().meta.label)}</p>
      <div>${presetChips}</div>
      <h3>ตัวชี้วัด (กดดูที่มา)</h3>
      <div class="metric-grid">${grid}</div>
      <h3>ย้อนหลัง</h3>
      <p class="chart-title">รายได้และกำไรสุทธิ (ล้านบาท)</p><div class="chart"><canvas id="c-rev" role="img" aria-label="กราฟรายได้และกำไรสุทธิ"></canvas></div>
      <p class="chart-title">ROE (%)</p><div class="chart"><canvas id="c-roe" role="img" aria-label="กราฟ ROE"></canvas></div>
      <p class="chart-title">ปันผลต่อหุ้นตามปีที่ขึ้น XD (บาท)</p><p class="chart-note">ที่มา: Yahoo dividends ทั้งสองโหมด</p><div class="chart"><canvas id="c-dps" role="img" aria-label="กราฟปันผลต่อหุ้น"></canvas></div>
      <p class="chart-title">FCF เทียบปันผลจ่าย (ล้านบาท)</p><div class="chart"><canvas id="c-fcf" role="img" aria-label="กราฟกระแสเงินสดอิสระเทียบปันผลจ่าย"></canvas></div>
      <details><summary>ดูเป็นตาราง</summary>
        <table><thead><tr><th class="l">ช่วง</th><th>รายได้</th><th>กำไร</th><th>ROE %</th><th>FCF</th><th>ปันผลจ่าย</th></tr></thead>
        <tbody>${h.p.map((p, i) => `<tr><td class="l">${esc(p)}</td><td>${fmt(h.rev[i], "ลบ.")}</td><td>${fmt(h.np[i], "ลบ.")}</td><td>${fmt(h.roe[i])}</td><td>${fmt(h.fcf[i], "ลบ.")}</td><td>${fmt(h.div[i], "ลบ.")}</td></tr>`).join("")}</tbody></table>
        <table><thead><tr><th class="l">ปี XD</th><th>DPS (บาท)</th></tr></thead>
        <tbody>${years.map((y) => `<tr><td class="l">${esc(y)}</td><td>${fmt(s.dps[y])}</td></tr>`).join("")}</tbody></table>
      </details>`;
    $("detail").hidden = false;
    drawCharts(s);
  }

  function closeDetail() {
    $("detail").hidden = true;
    charts.forEach((c) => c.destroy());
    charts = [];
  }

  function drawCharts(s) {
    charts.forEach((c) => c.destroy());
    charts = [];
    if (!window.Chart) {
      document.querySelectorAll(".chart").forEach((el) => { el.textContent = "โหลดไลบรารีกราฟไม่ได้ (ออฟไลน์?) — ดูข้อมูลในตารางด้านล่าง"; });
      return;
    }
    const css = getComputedStyle(document.documentElement);
    const c = (n) => css.getPropertyValue(n).trim();
    const s1 = c("--series-1"), s2 = c("--series-2"), surface = c("--surface"), grid = c("--grid"), ink = c("--text-2");
    const base = (legend) => ({
      responsive: true, maintainAspectRatio: false, animation: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: legend, position: "top", align: "end", labels: { color: ink, boxWidth: 10, boxHeight: 10, useBorderRadius: true, borderRadius: 2 } },
        tooltip: { callbacks: { label: (x) => `${x.dataset.label}: ${fmt(x.parsed.y, "")}` } },
      },
      scales: {
        x: { grid: { display: false }, ticks: { color: ink }, border: { color: grid } },
        y: { grid: { color: grid }, ticks: { color: ink, callback: (v) => fmt(v, Number.isInteger(v) ? "ลบ." : "") }, border: { display: false } },
      },
    });
    const bar = (label, data, color) => ({ label, data, backgroundColor: color, borderRadius: 4, borderSkipped: "start", maxBarThickness: 24, barPercentage: 0.8, categoryPercentage: 0.7 });
    const make = (id, cfg) => charts.push(new window.Chart($(id), cfg));
    make("c-rev", { type: "bar", data: { labels: s.h.p, datasets: [bar("รายได้", s.h.rev, s1), bar("กำไรสุทธิ", s.h.np, s2)] }, options: base(true) });
    make("c-roe", { type: "line", data: { labels: s.h.p, datasets: [{ label: "ROE %", data: s.h.roe, borderColor: s1, backgroundColor: s1, borderWidth: 2, pointRadius: 4, pointBorderColor: surface, pointBorderWidth: 2, spanGaps: false }] }, options: base(false) });
    const years = Object.keys(s.dps);
    make("c-dps", { type: "bar", data: { labels: years, datasets: [bar("DPS (บาท)", years.map((y) => s.dps[y]), s1)] }, options: base(false) });
    make("c-fcf", { type: "bar", data: { labels: s.h.p, datasets: [bar("FCF", s.h.fcf, s1), bar("ปันผลจ่าย", s.h.div, s2)] }, options: base(true) });
  }

  // ---------- ส่วนหัว ----------
  function renderHeader() {
    document.querySelectorAll(".source button").forEach((b) => {
      b.setAttribute("aria-pressed", b.dataset.source === state.source);
      b.disabled = !DATA[b.dataset.source];
      if (b.disabled) b.title = "ไม่มีไฟล์ข้อมูลนี้";
    });
    const m = ds().meta;
    $("badges").innerHTML = `<span>งบ: ${esc(m.period_note)}</span><span>ราคาปิด ${esc(m.price_date)}</span><span>สร้างข้อมูล ${esc(m.built)}</span>`;
    $("tabs").innerHTML = PRESETS.presets.map((p) => `<button type="button" role="tab" data-preset="${p.id}" aria-selected="${p.id === state.presetId}">${esc(p.name)}</button>`).join("");
    $("preset-desc").textContent = presetDef().desc;
    $("foot-source").textContent = m.source === "yahoo"
      ? `ข้อมูล: Yahoo Finance (finance.yahoo.com) ดึงเมื่อ ${m.fetched} · ราคาปิด ${m.price_date}`
      : `ข้อมูล: ${m.file_note} · ราคาและปันผลต่อหุ้นจาก Yahoo Finance ดึงเมื่อ ${m.fetched}`;
  }

  function renderAll() {
    renderHeader();
    renderPanel();
    renderTable();
  }

  // ---------- เหตุการณ์ ----------
  document.addEventListener("click", (e) => {
    const src = e.target.closest("[data-source]");
    if (src && !src.disabled) {
      state.source = src.dataset.source;
      const open = !$("detail").hidden && $("detail-title").textContent;
      renderAll();
      if (open) openDetail(open);
      return;
    }
    const tab = e.target.closest("[data-preset]");
    if (tab) { state.presetId = tab.dataset.preset; state.sort = { key: "score", dir: -1 }; return renderAll(); }
    const th = e.target.closest("th[data-sort]");
    if (th && th.closest("#table")) {
      const k = th.dataset.sort;
      state.sort = state.sort.key === k ? { key: k, dir: -state.sort.dir } : { key: k, dir: k === "t" ? 1 : -1 };
      return renderTable();
    }
    const num = e.target.closest("[data-src]");
    if (num) { e.stopPropagation(); return showPopover(num); }
    if (!e.target.closest("#popover")) $("popover").hidden = true;
    const tk = e.target.closest("[data-detail]");
    if (tk) return openDetail(tk.dataset.detail);
  });
  $("panel").addEventListener("input", onPanelInput);
  $("panel").addEventListener("change", onPanelInput);
  $("show-failed").addEventListener("change", (e) => { state.showFailed = e.target.checked; renderTable(); });
  $("search").addEventListener("input", (e) => { state.search = e.target.value; renderTable(); });
  $("reset").addEventListener("click", () => { Object.assign(state, fresh()); renderPanel(); renderTable(); });
  $("detail-close").addEventListener("click", closeDetail);
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (!$("popover").hidden) $("popover").hidden = true;
    else if (!$("detail").hidden) closeDetail();
  });

  if (!PRESETS || !SOURCES.length) {
    document.querySelector(".layout").innerHTML = "<p>ไม่พบไฟล์ข้อมูล — รัน <code>python screener/build.py</code> ก่อน</p>";
    return;
  }
  // ลิงก์ตรง: ?src=setsmart&preset=dividend&t=PTT
  const qs = new URLSearchParams(location.search);
  if (DATA[qs.get("src")]) state.source = qs.get("src");
  if (PRESETS.presets.some((p) => p.id === qs.get("preset"))) state.presetId = qs.get("preset");
  renderAll();
  if (qs.get("t")) openDetail(qs.get("t").toUpperCase());
  // จอแคบ: หุบแผงเกณฑ์ไว้ก่อน ให้ตารางขึ้นมาให้เห็นทันที
  if (matchMedia("(max-width: 860px)").matches) document.querySelector("#panel details").open = false;
})();
