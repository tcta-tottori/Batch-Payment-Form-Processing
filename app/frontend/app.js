"use strict";

const state = {
  files: [],
  sessionId: null,
  checklist: null,
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

function renderFileList() {
  const list = $("#file-list");
  list.innerHTML = "";
  state.files.forEach((f, idx) => {
    const row = document.createElement("div");
    row.className = "file-row";
    row.innerHTML =
      `<span><span class="badge">${idx + 1}</span>${escapeHtml(f.name)} (${formatBytes(f.size)})</span>` +
      `<button class="remove" data-idx="${idx}" title="削除">×</button>`;
    list.appendChild(row);
  });
  $("#process-btn").disabled = state.files.length === 0;
  $("#clear-btn").disabled = state.files.length === 0;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function formatBytes(n) {
  const units = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i ? 1 : 0)} ${units[i]}`;
}

function setStatus(text, kind = "") {
  const el = $("#status");
  el.textContent = text;
  el.className = "status " + kind;
}

function addFiles(files) {
  for (const f of files) {
    if (!/\.pdf$/i.test(f.name)) {
      setStatus(`PDF以外は無視します: ${f.name}`, "error");
      continue;
    }
    state.files.push(f);
  }
  renderFileList();
  setStatus(`${state.files.length}件のファイルを準備しました。`, "");
}

// --- DnD ---
const dropzone = $("#dropzone");
const fileInput = $("#file-input");
dropzone.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", (e) => addFiles(e.target.files));

["dragenter", "dragover"].forEach((ev) =>
  dropzone.addEventListener(ev, (e) => {
    e.preventDefault();
    dropzone.classList.add("dragover");
  }),
);
["dragleave", "drop"].forEach((ev) =>
  dropzone.addEventListener(ev, (e) => {
    e.preventDefault();
    dropzone.classList.remove("dragover");
  }),
);
dropzone.addEventListener("drop", (e) => {
  e.preventDefault();
  addFiles(e.dataTransfer.files);
});

$("#file-list").addEventListener("click", (e) => {
  const btn = e.target.closest(".remove");
  if (!btn) return;
  const idx = parseInt(btn.dataset.idx, 10);
  state.files.splice(idx, 1);
  renderFileList();
});

$("#clear-btn").addEventListener("click", () => {
  state.files = [];
  renderFileList();
  setStatus("");
});

$("#process-btn").addEventListener("click", async () => {
  if (state.files.length === 0) return;
  setStatus("PDFを解析しています…", "");
  $("#process-btn").disabled = true;
  const fd = new FormData();
  state.files.forEach((f) => fd.append("files", f, f.name));
  try {
    const res = await fetch("/api/v1/checklist", { method: "POST", body: fd });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    const data = await res.json();
    state.sessionId = data.sessionId;
    state.checklist = data.checklist;
    setStatus("解析が完了しました。", "success");
    renderChecklist();
  } catch (err) {
    setStatus(`エラー: ${err.message}`, "error");
  } finally {
    $("#process-btn").disabled = false;
  }
});

$("#reset-btn").addEventListener("click", () => {
  if (state.sessionId) {
    fetch(`/api/v1/checklist/${state.sessionId}`, { method: "DELETE" }).catch(() => {});
  }
  state.files = [];
  state.sessionId = null;
  state.checklist = null;
  renderFileList();
  $("#result-section").classList.add("hidden");
  $("#warning-section").classList.add("hidden");
  setStatus("");
});

$("#download-md").addEventListener("click", async () => {
  if (!state.sessionId) return;
  const url = `/api/v1/checklist/${state.sessionId}/markdown`;
  const blob = await (await fetch(url)).blob();
  triggerDownload(blob, `一括納付明細書チェックリスト_${monthSlug()}.md`);
});

$("#download-tsv").addEventListener("click", async () => {
  if (!state.sessionId) return;
  const url = `/api/v1/checklist/${state.sessionId}/tsv`;
  const blob = await (await fetch(url)).blob();
  triggerDownload(blob, `一括納付明細書チェックリスト_${monthSlug()}.tsv`);
});

$("#download-permits").addEventListener("click", async () => {
  if (!state.sessionId) return;
  setStatus("許可通知書PDFを生成しています…", "");
  try {
    const res = await fetch(`/api/v1/checklist/${state.sessionId}/permits.pdf`);
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    const blob = await res.blob();
    triggerDownload(blob, `許可通知書_抽出_${monthSlug()}.pdf`);
    setStatus("許可通知書PDFをダウンロードしました。", "success");
  } catch (err) {
    setStatus(`エラー: ${err.message}`, "error");
  }
});

function triggerDownload(blob, filename) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => {
    URL.revokeObjectURL(a.href);
    a.remove();
  }, 100);
}

function monthSlug() {
  const m = state.checklist && state.checklist.month;
  return m ? m.replace("-", "") : "out";
}

document.addEventListener("click", async (e) => {
  const btn = e.target.closest(".copy-btn");
  if (!btn || !state.sessionId) return;
  const section = btn.dataset.section;
  const url = `/api/v1/checklist/${state.sessionId}/tsv?section=${encodeURIComponent(section)}`;
  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const text = await res.text();
    await navigator.clipboard.writeText(text);
    setStatus(`「${btn.textContent.trim()}」: クリップボードにコピーしました。`, "success");
  } catch (err) {
    setStatus(`コピーに失敗しました: ${err.message}`, "error");
  }
});

function renderChecklist() {
  const cl = state.checklist;
  if (!cl) return;
  $("#result-section").classList.remove("hidden");

  const yen = (n) => `¥${(n || 0).toLocaleString()}`;
  $("#meta").innerHTML = [
    `<div><strong>対象月:</strong> ${cl.month || "(未判定)"}</div>`,
    `<div><strong>納期限:</strong> ${cl.deadline || "(未判定)"}</div>`,
    `<div><strong>作成日:</strong> ${cl.created_at || ""}</div>`,
  ].join("");

  // Page counts
  const counts = ['<table><thead><tr><th>#</th><th>会社名</th><th>表紙</th><th>許可通知書</th><th>印刷枚数</th><th>備考</th></tr></thead><tbody>'];
  cl.companies.forEach((c, i) => {
    const total = (c.total_cover_pages || 0) + (c.total_permit_pages || 0);
    const note = c.manual_permit_check ? "(手動確認)" : "";
    counts.push(
      `<tr><td>${i + 1}</td><td>${escapeHtml(c.name)}</td>` +
      `<td class="num">${c.total_cover_pages}</td>` +
      `<td class="num">${c.total_permit_pages}</td>` +
      `<td class="num">${total}</td><td>${note}</td></tr>`,
    );
  });
  counts.push("</tbody></table>");
  $("#company-counts").innerHTML = counts.join("");

  // Details
  const details = ['<table><thead><tr><th>#</th><th>会社名</th><th>納付番号</th><th>受入科目</th><th>税額</th><th>申告官署</th><th>納期限</th></tr></thead><tbody>'];
  let no = 0;
  cl.companies.forEach((c) => {
    c.details.forEach((pd) => {
      no += 1;
      details.push(
        `<tr><td>${no}</td><td>${escapeHtml(c.name)}</td>` +
        `<td>${escapeHtml(pd.payment_number || "")}</td>` +
        `<td>${escapeHtml(pd.subject_name || "")}</td>` +
        `<td class="num">${yen(pd.total_amount)}</td>` +
        `<td>${escapeHtml(pd.customs_office || "")}</td>` +
        `<td>${escapeHtml(pd.deadline || "")}</td></tr>`,
      );
    });
  });
  details.push("</tbody></table>");
  $("#company-details").innerHTML = details.join("");

  // Validations
  const v = ['<table><thead><tr><th>種類</th><th>一括納付書番号</th><th>受入科目</th><th>期待値</th><th>実際値</th><th>差額</th><th>結果</th></tr></thead><tbody>'];
  cl.validations.forEach((row) => {
    const cls = row.match ? "validation-ok" : "validation-ng";
    const icon = row.match ? "✓ 一致" : "✗ 不一致";
    const trCls = row.match ? "" : "warn";
    v.push(
      `<tr class="${trCls}"><td>${escapeHtml(row.type)}</td>` +
      `<td>${escapeHtml(row.bulk_payment_number || "")}</td>` +
      `<td>${escapeHtml(row.subject_name || "")}</td>` +
      `<td class="num">${yen(row.expected)}</td>` +
      `<td class="num">${yen(row.actual)}</td>` +
      `<td class="num">${yen(row.diff)}</td>` +
      `<td class="${cls}">${icon}</td></tr>`,
    );
  });
  v.push("</tbody></table>");
  $("#validations").innerHTML = v.join("");

  // Invoice mappings
  const invHtml = [];
  cl.companies.forEach((c) => {
    if (!c.invoice_mappings || c.invoice_mappings.length === 0) return;
    invHtml.push(`<h4>${escapeHtml(c.name)}</h4>`);
    c.invoice_mappings.forEach((m) => {
      invHtml.push(`<div class="invoice-block">`);
      invHtml.push(`<h4>一括納付書 ${escapeHtml(m.bulk_payment_number)} / ${escapeHtml(m.customs_office || "")} / ${escapeHtml(m.subject_name)}</h4>`);
      invHtml.push('<table><thead><tr><th>#</th><th>本税調定日</th><th>輸入申告番号</th><th>仕入書番号下5桁</th></tr></thead><tbody>');
      m.items.forEach((it) => {
        const labelMap = {
          matched: "",
          not_recorded: "(記載なし)",
          unmatched: "(未特定)",
          not_in_invoice: "(当月請求書に未掲載)",
        };
        const cell = it.invoice_last5 && it.invoice_last5.length
          ? it.invoice_last5.join(", ")
          : labelMap[it.status] || "";
        invHtml.push(
          `<tr><td>${it.no}</td><td>${escapeHtml(it.settled_date || "")}</td>` +
          `<td>${escapeHtml(it.declaration_number)}</td>` +
          `<td>${escapeHtml(cell)}</td></tr>`,
        );
      });
      invHtml.push("</tbody></table>");
      invHtml.push("</div>");
    });
  });
  $("#invoice-mappings").innerHTML = invHtml.length
    ? invHtml.join("")
    : '<p class="hint">許可通知書ファイルがアップロードされていないため、仕入書番号対応表は生成されません。</p>';

  // Attached files
  const af = ['<table><thead><tr><th>#</th><th>種別</th><th>ファイル名</th><th>ページ数</th></tr></thead><tbody>'];
  cl.uploaded_files.forEach((f, i) => {
    af.push(
      `<tr><td>${i + 1}</td><td>${escapeHtml(f.pdf_kind)}</td>` +
      `<td>${escapeHtml(f.filename)}</td>` +
      `<td class="num">${f.pages}</td></tr>`,
    );
  });
  af.push("</tbody></table>");
  $("#attached-files").innerHTML = af.join("");

  // Warnings
  if (cl.warnings && cl.warnings.length) {
    $("#warning-section").classList.remove("hidden");
    $("#warning-list").innerHTML = cl.warnings
      .map((w) => `<li>${escapeHtml(w)}</li>`).join("");
  } else {
    $("#warning-section").classList.add("hidden");
  }

  $("#result-section").scrollIntoView({ behavior: "smooth" });
}
