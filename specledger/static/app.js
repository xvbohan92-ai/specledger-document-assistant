"use strict";
import {formatted} from "/static/value-format.mjs";
const $ = (id) => document.getElementById(id);
const state = {meta: null, documents: [], current: null, field: null, page: 1, evidence: null, busy: false, correctionField: null};
const statuses = {ready: "待确认", needs_review: "需要复核", approved: "已入库"};
const issueNames = {incompatible_signal: "单位不能带 AC/DC", precision_loss: "超出数值精度", missing_field: "未提取到字段", missing_value: "缺失值", unsupported_unit: "未知单位", conflicting_values: "原文冲突", unparsed_value_or_unit: "数值或范围待核查", invalid_model: "型号格式待核查", invalid_ip_rating: "防护等级待核查", outside_supported_range: "超出支持范围"};
function el(tag, text, cls) { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (cls) node.className = cls; return node; }
function error(message) { $("error").textContent = message || ""; $("error").hidden = !message; for (const id of ["correction-error", "reparse-error"]) { $(id).textContent = message || ""; $(id).hidden = !message; } }
async function api(path, options = {}) {
  const response = await fetch(path, {...options, signal: AbortSignal.timeout(240000)});
  const body = await response.json();
  if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : "输入格式无效，请检查参数。");
  return body;
}
function json(method, body) { return {method, headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)}; }
function view(name) {
  for (const key of ["review", "query", "evaluation"]) { $(`${key}-page`).hidden = key !== name; $(`nav-${key}`).classList.toggle("active", key === name); }
  $("mobile-view").value = name; window.scrollTo(0, 0);
}
function busy(value) {
  state.busy = value; $("processing").hidden = !value; $("reparse-progress").hidden = !value;
  for (const node of document.querySelectorAll("main button, main input, main textarea, main select, dialog button, dialog input, dialog textarea")) {
    if (node.id !== "mobile-view") node.disabled = value;
  }
  if (!value) {
    $("approve").disabled = !state.current || state.current.status !== "ready"; $("reparse").disabled = !state.current;
    $("previous-page").disabled = !state.current || state.page <= 1;
    $("next-page").disabled = !state.current || state.page >= state.current.pages.length;
  }
}
async function refresh() {
  const [meta, documents] = await Promise.all([api("/api/meta"), api("/api/documents")]);
  const before = state.documents.map((d) => `${d.id}:${d.revision}:${d.status}`).join("|");
  const after = documents.map((d) => `${d.id}:${d.revision}:${d.status}`).join("|");
  if (before && before !== after) $("query-results").replaceChildren(el("div", "文档版本已更新，请重新查询当前已审核记录。", "empty"));
  state.meta = meta; state.documents = documents;
  $("stat-documents").textContent = `${meta.documents} / ${meta.pages}`;
  $("stat-approved").textContent = String(meta.approved).padStart(2, "0");
  $("stat-review").textContent = String(meta.needs_review).padStart(2, "0");
  $("backend-label").textContent = (meta.backend.kind === "docling" ? "Docling · 本地 OCR" : "文本基线 · 无 OCR") + (meta.backend.field_mapper ? ` · ${meta.backend.field_mapper.variant === "finetuned" ? "微调" : "预训练"}字段映射` : "");
  renderDocuments();
}
function renderDocuments() {
  const list = $("document-list"); list.replaceChildren(); $("document-count").textContent = state.documents.length;
  if (!state.documents.length) list.append(el("p", "上传 PDF 或载入演示开始。", "empty"));
  for (const row of state.documents) {
    const button = el("button", undefined, "document-card"); button.classList.toggle("active", row.id === state.current?.id);
    button.append(el("strong", row.filename), el("p", `${row.page_count} 页 · ${row.cache_hit ? "读取缓存" : "解析"} ${row.processing_ms.toFixed(0)} ms`), el("small", `${statuses[row.status]}${row.issue_count ? ` · ${row.issue_count} 项问题` : ""}`));
    button.addEventListener("click", () => loadDocument(row.id)); list.append(button);
  }
}
async function action(callback) {
  if (state.busy) return; error(""); busy(true);
  try { await callback(); }
  catch (e) { error(e.message); }
  finally { busy(false); }
}
async function loadDocument(id, field = null) {
  await action(async () => { state.current = await api(`/api/documents/${id}`); state.field = field; state.page = 1; renderDocument(); renderDocuments(); });
}
function renderDocument() {
  const doc = state.current; if (!doc) return;
  $("document-title").textContent = doc.filename;
  $("document-status").textContent = statuses[doc.status]; $("document-status").className = "badge" + (doc.issues.length ? " warning" : doc.status === "approved" ? " approved" : "");
  $("document-description").textContent = `${doc.pages.length} 页 · revision ${doc.revision} · ${doc.backend.kind === "docling" ? "Docling / RapidOCR" : "PDF 文本基线"} · ${doc.source}`;
  const content = $("field-content"); content.replaceChildren();
  const table = el("table"), heading = el("tr");
  for (const name of ["字段", "原文值", "归一化值", "页码 / 状态", "操作"]) heading.append(el("th", name)); table.append(heading);
  for (const [name, schema] of Object.entries(state.meta.schema)) {
    const record = doc.fields[name], candidates = doc.observations.filter((o) => o.name === name);
    const issue = doc.issues.find((i) => i.field === name), row = el("tr"); row.dataset.field = name;
    row.classList.toggle("selected", state.field === name);
    const fieldCell = el("td"), fieldButton = el("button", schema.label, "field-select field-name"); fieldButton.addEventListener("click", () => selectField(name)); fieldCell.append(fieldButton);
    const raw = record?.raw_value ?? candidates[0]?.raw_value ?? "—";
    const value = el("td", formatted(record?.value, record?.unit, record?.signal, record?.exact_value), "normalized-value" + (issue ? " missing" : ""));
    const provenance = record ? `p.${record.evidence.page} · ${record.origin === "manual" ? "人工修订" : record.mapping ? "模型映射 · 单位校验" : "单位校验"}` : issueNames[issue?.code] || "待复核";
    const operation = el("td");
    const source = el("button", "原文", "field-action"); source.addEventListener("click", () => selectField(name)); operation.append(source);
    if (doc.status !== "approved") { const edit = el("button", "修订", "field-action"); edit.style.marginLeft = "10px"; edit.addEventListener("click", () => openCorrection(name)); operation.append(edit); }
    row.append(fieldCell, el("td", raw, "raw-value"), value, el("td", provenance, issue ? "missing" : ""), operation); table.append(row);
  }
  const wrap = el("div", undefined, "table-wrap"); wrap.append(table); content.append(wrap);
  $("review-note").textContent = doc.status === "approved" ? "已入库。修订记录与来源已保留。" : doc.issues.length ? `${doc.issues.length} 项问题需修订，暂不能入库。` : "字段校验通过，请核对原文后确认入库。";
  $("approve").textContent = doc.status === "approved" ? "已确认入库 ✓" : "确认并入库 ↗";
  const audit = $("audit-content"); audit.replaceChildren();
  for (const event of doc.audit) {
    const item = el("div", undefined, "audit-event");
    item.append(el("strong", `r${event.revision} · ${{extracted: "提取完成", corrected: "人工修订", approved: "确认入库", reprocessed: "重新解析", rules_revalidated: "单位规则升级"}[event.action]} · ${event.created_at} UTC`));
    if (event.action === "corrected") item.append(el("p", `${state.meta.schema[event.details.field].label} → ${event.details.raw_value}；p.${event.details.page}；${event.details.reason}`));
    if (event.details.reason && event.action !== "corrected") item.append(el("p", event.details.reason));
    audit.append(item);
  }
  $("open-pdf").href = `/api/documents/${doc.id}/pdf`; $("open-pdf").hidden = false;
  renderHistory(doc);
  selectField(state.field && state.meta.schema[state.field] ? state.field : "model");
}
function selectField(name) {
  state.field = name;
  for (const row of document.querySelectorAll("tr[data-field]")) row.classList.toggle("selected", row.dataset.field === name);
  const doc = state.current, candidates = doc.observations.filter((o) => o.name === name), content = $("evidence-lines"); content.replaceChildren();
  $("source-title").textContent = `${state.meta.schema[name].label} · 原文证据`;
  for (const observation of candidates) {
    const button = el("button", undefined, "evidence-item");
    button.append(el("span", `p.${observation.evidence.page} · ${observation.origin === "manual" ? "人工修订" : observation.evidence.precision === "table" ? "表格级位置" : "原文位置"}`), el("strong", observation.raw_value), el("p", observation.reason || observation.evidence.text));
    if (observation.mapping) button.append(el("p", `原标签：${observation.mapping.raw_label}；模型只识别字段类型，数值仍取自这一行原文。`));
    button.addEventListener("click", () => showEvidence(observation)); content.append(button);
  }
  if (!candidates.length) content.append(el("p", "没有提取到这个字段。可以核查原文，再补录并写明理由。", "muted"));
  showEvidence(doc.fields[name] || candidates[0] || null);
}
function showEvidence(observation) {
  state.evidence = observation; if (observation) state.page = observation.evidence.page;
  renderPage();
}
function renderPage() {
  const doc = state.current; if (!doc) return;
  $("page-sheet").hidden = false; $("page-image").src = `/api/documents/${doc.id}/pages/${state.page}`;
  $("page-image").alt = `${doc.filename} 第 ${state.page} 页`;
  $("page-label").textContent = `${state.page} / ${doc.pages.length}`;
  $("previous-page").disabled = state.page <= 1 || state.busy; $("next-page").disabled = state.page >= doc.pages.length || state.busy;
  const observation = state.evidence, box = $("evidence-box");
  box.hidden = !observation || observation.evidence.page !== state.page || observation.origin === "manual";
  if (!box.hidden) { const [l,t,r,b] = observation.evidence.bbox; box.style.left = `${l*100}%`; box.style.top = `${t*100}%`; box.style.width = `${(r-l)*100}%`; box.style.height = `${(b-t)*100}%`; }
  $("evidence-description").textContent = observation?.origin === "manual" ? "这是人工修订值，原始候选仍在右侧保留；该值不被伪装成 OCR 原文。" : observation ? "高亮框定位提取来源。页码可追溯；位置精度以行、单元格或整张表为准。" : "当前字段没有原文定位，请人工核查页面。";
}
function openCorrection(name) {
  error(""); state.correctionField = name; const doc = state.current, observation = doc.fields[name] || doc.observations.find((o) => o.name === name);
  $("correction-title").textContent = `修订 · ${state.meta.schema[name].label}`;
  $("correction-value").value = observation?.raw_value || ""; $("correction-page").value = observation?.evidence.page || 1;
  $("correction-page").max = doc.pages.length; $("correction-reason").value = ""; $("correction-dialog").showModal();
}
function renderHistory(doc) {
  const content = $("history-content"); content.replaceChildren();
  const labels = {extracted: "首次提取", corrected: "人工修订", approved: "批准入库", reprocessed: "重新解析", legacy_import: "升级前已有状态", rules_revalidated: "单位规则升级"};
  for (const version of doc.versions || []) {
    const button = el("button", undefined, "history-item");
    button.append(el("strong", `r${version.revision} · ${statuses[version.status]} · ${labels[version.action] || "版本快照"}${version.revision === doc.revision ? " · 当前" : ""}`), el("small", `${version.backend} · ${version.created_at} UTC${version.reason ? " · " + version.reason : ""}`));
    button.addEventListener("click", () => showVersion(doc.id, version.revision)); content.append(button);
  }
}
async function showVersion(id, revision) {
  await action(async () => {
    const snapshot = await api(`/api/documents/${id}/versions/${revision}`);
    $("history-title").textContent = `${snapshot.filename} · r${snapshot.revision}`;
    $("history-description").textContent = `只读历史 · ${statuses[snapshot.status]} · ${snapshot.backend.kind}。当前为 r${snapshot.latest_revision}，历史数据不会作为当前查询结果。`;
    const content = $("history-fields"); content.replaceChildren();
    const table = el("table"), head = el("tr");
    for (const text of ["字段", "原值", "规范值", "来源"]) head.append(el("th", text)); table.append(head);
    for (const [name, schema] of Object.entries(state.meta.schema)) {
      const record = snapshot.fields[name], candidate = snapshot.observations.find((o) => o.name === name), row = el("tr");
      row.append(el("td", schema.label), el("td", record?.raw_value ?? candidate?.raw_value ?? "—"), el("td", formatted(record?.value, record?.unit, record?.signal, record?.exact_value)), el("td", record ? `p.${record.evidence.page} · ${record.origin === "manual" ? "人工修订" : "原文"}` : "未解决"));
      table.append(row);
    }
    content.append(table); $("history-dialog").showModal();
  });
}
$("close-history").addEventListener("click", () => $("history-dialog").close());
$("reparse").addEventListener("click", () => { error(""); $("reparse-reason").value = ""; $("reparse-dialog").showModal(); });
$("reparse-dialog").addEventListener("cancel", (event) => { if (state.busy) event.preventDefault(); });
$("cancel-reparse").addEventListener("click", () => { $("reparse-dialog").close(); error(""); });
$("reparse-form").addEventListener("submit", (event) => {
  event.preventDefault(); const doc = state.current, reason = $("reparse-reason").value;
  action(async () => {
    state.current = await api(`/api/documents/${doc.id}/reparse`, json("POST", {revision: doc.revision, reason}));
    $("reparse-dialog").close(); state.field = null; await refresh(); renderDocument();
  });
});
$("correction-form").addEventListener("submit", async (event) => {
  event.preventDefault(); const name = state.correctionField, doc = state.current;
  const payload = {raw_value: $("correction-value").value, reason: $("correction-reason").value, page: Number($("correction-page").value), revision: doc.revision};
  await action(async () => { state.current = await api(`/api/documents/${doc.id}/fields/${name}`, json("PATCH", payload)); $("correction-dialog").close(); await refresh(); renderDocument(); });
});
$("cancel-correction").addEventListener("click", () => { $("correction-dialog").close(); error(""); });
$("approve").addEventListener("click", () => action(async () => {
  state.current = await api(`/api/documents/${state.current.id}/approve`, json("POST", {revision: state.current.revision})); await refresh(); renderDocument();
}));
for (const [id, delta] of [["previous-page", -1], ["next-page", 1]]) $(id).addEventListener("click", () => { state.page += delta; state.evidence = null; renderPage(); });
$("upload-form").addEventListener("submit", (event) => {
  event.preventDefault(); const file = $("pdf-file").files[0]; if (!file) return;
  if (file.size > 32 * 1024 * 1024) { error("PDF 大小不能超过 32MB。"); return; }
  const form = new FormData(); form.append("file", file); form.append("source", $("upload-source").value);
  action(async () => { const result = await api("/api/upload", {method: "POST", body: form}); state.current = result.document; state.field = null; await refresh(); renderDocument(); });
});
$("import-example").addEventListener("click", () => action(async () => {
  const id = $("example-select").value; if (!id) throw new Error("尚未生成演示文档，请运行 specledger demo。");
  const result = await api(`/api/examples/${id}`, {method: "POST"}); state.current = result.document; state.field = null; await refresh(); renderDocument();
}));
async function query() {
  const field = $("query-field").value;
  await action(async () => {
    const content = $("query-results"), op = $("query-op").value, raw = $("query-value").value;
    content.replaceChildren(el("p", "正在查询已审核记录…", "muted"));
    let report;
    try { report = await api("/api/query", json("POST", {filters: [{field, op, raw_value: raw}]})); }
    catch (e) { content.replaceChildren(el("div", "本次查询失败，请检查条件后重试。", "empty")); throw e; }
    content.replaceChildren(el("p", `${state.meta.schema[field].label} ${op} ${raw} · ${report.count} 条已审核记录；${report.note}`, "muted"));
    if (!report.count) content.append(el("div", "没有符合条件的已审核记录。待审文档不会出现在查询中。", "empty"));
    for (const row of report.results) {
      const card = el("article", undefined, "panel result-card"); card.append(el("h2", row.model), el("p", `${row.filename} · ${row.source}`, "muted"));
      const values = el("div", undefined, "result-values");
      for (const [name, record] of Object.entries(row.evidence)) {
        const button = el("button"); button.append(el("span", state.meta.schema[name].label), el("strong", formatted(record.value, record.unit, record.signal, record.exact_value)), el("span", `查看 p.${record.evidence.page}${record.origin === "manual" ? " · 人工修订" : " 原文"}`));
        button.addEventListener("click", async () => { view("review"); await loadDocument(row.document_id, name); $("source-title").scrollIntoView({block: "start"}); }); values.append(button);
      }
      card.append(values); content.append(card);
    }
    const details = el("details", undefined, "query-sql"); details.append(el("summary", "查看参数化查询与条件"), el("pre", `${report.sql}\nParameters: ${JSON.stringify(report.parameters)}`)); content.append(details);
  });
}
$("query-form").addEventListener("submit", (event) => { event.preventDefault(); query(); });
$("query-field").addEventListener("change", () => {
  const name = $("query-field").value; if (["model", "ip_rating"].includes(name)) $("query-op").value = "=";
  $("query-value").value = name === "model" ? "ALP-100" : name === "ip_rating" ? "IP65" : `100 ${state.meta.schema[name].unit}`;
});
for (const button of document.querySelectorAll("[data-field][data-value]")) button.addEventListener("click", () => { $("query-field").value = button.dataset.field; $("query-value").value = button.dataset.value; $("query-op").value = button.dataset.field === "ip_rating" ? "=" : "<="; query(); });
function renderEvaluation(report) {
  const content = $("evaluation-content"); content.replaceChildren();
  if (!report.available) { content.append(el("div", `旧版 OCR 基线：${report.reason || "暂无与当前后端匹配的记录。"}`, "panel eval-card")); return; }
  const card = el("article", undefined, "panel eval-card"); const result = report.summary.docling;
  card.append(el("h2", `${result.documents} 份测试文档 · ${result.pages} 页 · ${result.field_total} 个字段`), el("p", "自制英文设备表与同作者标注；4 个开发文档与 8 个测试文档分开，不代表独立真实厂商文档上的效果。"));
  const chart = el("div", undefined, "eval-bars");
  for (const [mode, label] of [["text", "仅 PDF 文本"], ["docling", "Docling + OCR + 校验"]]) {
    const v = report.summary[mode], series = el("div", undefined, "eval-series"); series.append(el("h3", label));
    for (const [metric, title] of [["field_accuracy", "字段值 / 状态正确率"], ["digital_accuracy", "数字 PDF"], ["scan_accuracy", "扫描 PDF"]]) {
      const track = el("div", undefined, "bar-track"), fill = el("div"); fill.style.width = `${v[metric] * 100}%`; track.append(fill); track.setAttribute("aria-hidden", "true"); series.append(el("p", `${title} ${(v[metric]*100).toFixed(1)}%`), track);
    } chart.append(series);
  }
  card.append(chart);
  const table = el("table"), h = el("tr"); for (const label of ["流程", "字段正确", "页码召回", "错误放行", "正常文档误拦", "P50 ms"]) h.append(el("th", label)); table.append(h);
  for (const [mode, v] of Object.entries(report.summary)) {
    const row = el("tr"); for (const value of [mode === "docling" ? "Docling + 校验" : "PDF 文本 + 校验", `${v.field_correct}/${v.field_total}`, `${(v.page_attribution_recall*100).toFixed(1)}%`, `${v.invalid_passed}/${v.invalid_documents}`, `${v.clean_blocked}/${v.clean_documents}`, v.p50_ms.toFixed(0)]) row.append(el("td", value)); table.append(row);
  }
  const wrap = el("div", undefined, "table-wrap"); wrap.append(table); card.append(wrap, el("p", "字段指标同时检查规范值与未解决状态，不是 OCR 字符准确率。页码召回不衡量框的位置精度。延迟关闭缓存、排除模型初始化，含 PDF 解析与字段规则。")); content.append(card);
  const ablation = el("article", undefined, "panel eval-card"); ablation.append(el("h2", "单位归一化的作用"), el("p", `同一 Docling 输出上，刻意只取第一个数字、不换算单位的基线为 ${result.naive_numeric_correct}/${result.numeric_total}；加入归一化后为 ${result.normalized_numeric_correct}/${result.numeric_total}。这不是另一个 OCR 模型或训练结果。`)); content.append(ablation);
  const failures = report.records.filter((r) => r.pipeline === "docling" && (r.error || r.mistakes.length));
  if (failures.length) { const failureCard = el("article", undefined, "panel eval-card"); failureCard.append(el("h2", "保留失败案例")); for (const row of failures) failureCard.append(el("p", `${row.case_id} · ${row.error || row.mistakes.map((m) => `${state.meta.schema[m.field].label}：期望 ${m.expected}，实际 ${m.actual}`).join("；")}`, "eval-error")); content.append(failureCard); }
}
function renderFinetuning(report) {
  if (!report.available) return;
  const card = el("article", undefined, "panel eval-card"); card.id = "finetuning-evidence";
  card.append(el("h2", "字段映射微调 · 固定前后对照"), el("p", `${report.parameters.toLocaleString()} 个参数；训练 ${report.rows.train} / 验证 ${report.rows.valid} / 测试 ${report.rows.test} 条字段标签，按验证集选中第 ${report.selected_epoch} 轮。只训练字段名称映射，OCR 权重保持冻结。`));
  const table = el("table"), head = el("tr");
  for (const text of ["模型", "标签正确", "Macro F1", "新 PDF 字段值 / 状态正确"]) head.append(el("th", text)); table.append(head);
  for (const [mode, name] of [["base", "预训练映射"], ["finetuned", "微调映射"]]) {
    const v = report.components[mode], row = el("tr");
    for (const text of [name, `${v.correct}/${v.rows}`, v.macro_f1.toFixed(3), `${report.raw[mode].field_correct}/${report.raw[mode].field_total}`]) row.append(el("td", text)); table.append(row);
  }
  const wrap = el("div", undefined, "table-wrap"); wrap.append(table); card.append(wrap);
  card.append(el("p", `原始模型的 PDF 字段正确率提高 ${report.raw_delta_pp.toFixed(2)} 个百分点；两组均误接收全部 6 条干扰标签，没有一份新测试文档完全正确。16 份测试 PDF 共 112 个字段槽位，数字 / 扫描各 8 份，复用同一份实际 OCR 输出。`));
  const figure = el("img"); figure.src = "/api/finetuning-figure"; figure.className = "training-figure"; figure.alt = "字段标签测试、固定 OCR 文档测试、验证选点以及单独标识的事后工程诊断"; card.append(figure);
  card.append(el("h3", "追加工程诊断 · 复用测试集"));
  const base = report.scope_diagnostic.base, tuned = report.scope_diagnostic.finetuned;
  card.append(el("p", `失败分析后增加字段范围检查：同一检查下，预训练模型 ${base.correct}/${base.total}，微调模型 ${tuned.correct}/${tuned.total}。这是事后规则诊断，不能作为独立留出测试或纯微调收益。仍有 ${tuned.clean_blocked}/12 份正常文档需复核；问题文档没有被标为可直接确认。`));
  card.append(el("p", "范围：全部标签和多版式 PDF 均为本项目构造，未经独立人工标注，不代表真实厂商文档或通用 OCR 效果。任何校验通过的文档仍需人工批准。旧版 56 个字段的测试另列。"));
  $("evaluation-content").prepend(card);
}
for (const name of ["review", "query", "evaluation"]) $(`nav-${name}`).addEventListener("click", () => view(name));
$("mobile-view").addEventListener("change", (event) => view(event.target.value));
async function init() {
  try {
    await refresh();
    for (const [name, schema] of Object.entries(state.meta.schema)) { const option = el("option", schema.label); option.value = name; $("query-field").append(option); }
    $("query-field").value = "power_w";
    const examples = await api("/api/examples"); for (const row of examples) { const option = el("option", `${row.id} · ${row.kind === "scan" ? "扫描 PDF" : "数字 PDF"}`); option.value = row.id; $("example-select").append(option); }
    if (state.documents.length) await loadDocument(state.documents[0].id);
    const backend = $("backend-details"); backend.append(el("p", state.meta.backend.note));
    for (const model of state.meta.backend.models) backend.append(el("p", `${model.repo}\nrevision: ${model.revision}`));
    try { renderEvaluation(await api("/api/evaluation")); } catch { renderEvaluation({available: false, reason: "评测报告暂不可用，文档审阅仍可使用。"}); }
    try { renderFinetuning(await api("/api/finetuning")); } catch { $("evaluation-content").append(el("p", "微调实验记录暂不可用。")); }
  } catch (e) { error(e.message); }
}
init();
