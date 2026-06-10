const money = new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY" });
let activeInvoiceJobId = "";
let invoiceJobTimer = 0;
let financeState = { reimbursements: [], selectedMonth: "" };

async function loadFinance() {
  const res = await fetch("/api/finance");
  const data = await res.json();
  setSelectedInvoiceDir(data.last_invoice_dir || "");
  financeState.reimbursements = data.reimbursements || [];
  financeState.selectedMonth = financeState.selectedMonth || data.current_month || currentBrowserMonth();
  renderMonthFilter(financeState.reimbursements, financeState.selectedMonth);
  renderSelectedReimbursements();
}

function renderMonthFilter(rows, selectedMonth) {
  const yearSelect = document.getElementById("reimbursementYear");
  const monthSelect = document.getElementById("reimbursementMonth");
  if (!yearSelect || !monthSelect) {
    return;
  }
  const [selectedYear, selectedMonthNumber] = splitMonthKey(selectedMonth);
  const years = new Set(rows.map(row => splitMonthKey(row.month)[0]).filter(Boolean));
  for (let year = 2026; year <= 2050; year += 1) {
    years.add(String(year));
  }
  yearSelect.innerHTML = [...years].sort((a, b) => Number(b) - Number(a))
    .map(year => `<option value="${escapeAttr(year)}"${year === selectedYear ? " selected" : ""}>${escapeHtml(year)}年</option>`)
    .join("");
  monthSelect.innerHTML = Array.from({ length: 12 }, (_item, index) => String(index + 1).padStart(2, "0"))
    .map(month => `<option value="${month}"${month === selectedMonthNumber ? " selected" : ""}>${Number(month)}月</option>`)
    .join("");
}

function setupMonthFilter() {
  const yearSelect = document.getElementById("reimbursementYear");
  const monthSelect = document.getElementById("reimbursementMonth");
  if (!yearSelect || !monthSelect) {
    return;
  }
  const onChange = () => {
    financeState.selectedMonth = `${yearSelect.value}-${monthSelect.value}`;
    renderSelectedReimbursements();
  };
  yearSelect.addEventListener("change", onChange);
  monthSelect.addEventListener("change", onChange);
}

function renderSelectedReimbursements() {
  renderReimbursements(financeState.reimbursements.filter(row => row.month === financeState.selectedMonth));
}

function renderReimbursements(rows) {
  const tbody = document.getElementById("reimbursementRows");
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="6" class="muted">${monthLabel(financeState.selectedMonth)}暂无已登记报销批次</td></tr>`;
    return;
  }
  const html = [];
  rows.forEach(row => {
    const batches = [...(row.batches || [])].sort((a, b) => String(a.reimbursed_at || "").localeCompare(String(b.reimbursed_at || "")));
    if (!batches.length) {
      html.push(`<tr class="month-total">
        <td>${escapeHtml(row.label)}</td>
        <td>合计</td>
        <td>${row.file_count}</td>
        <td>${row.included_count}</td>
        <td>${money.format(row.total_amount || 0)}</td>
        <td></td>
      </tr>`);
      return;
    }
    batches.forEach((batch, index) => {
      html.push(`<tr class="batch-row">
        <td>${index === 0 ? escapeHtml(row.label) : ""}</td>
        <td>${formatBatchLabel(batch, index)}</td>
        <td>${batch.file_count || 0}</td>
        <td>${batch.included_count || 0}</td>
        <td>${money.format(Number(batch.total_amount || 0))}</td>
        <td>${renderBatchActions(batch)}</td>
      </tr>`);
    });
    if (batches.length > 1) {
      html.push(`<tr class="month-total">
        <td>${escapeHtml(row.label)}</td>
        <td>合计 ${row.batch_count} 批</td>
        <td>${row.file_count}</td>
        <td>${row.included_count}</td>
        <td>${money.format(row.total_amount || 0)}</td>
        <td></td>
      </tr>`);
    }
  });
  tbody.innerHTML = html.join("");
  tbody.querySelectorAll(".open-file").forEach(button => {
    button.addEventListener("click", async () => {
      await fetch("/api/open-file", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: button.dataset.path })
      });
    });
  });
}

function currentBrowserMonth() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

function monthLabel(month) {
  const match = String(month || "").match(/^([0-9]{4})-([0-9]{2})$/);
  if (!match) {
    return month || "当前月份";
  }
  return `${match[1]}年${Number(match[2])}月`;
}

function splitMonthKey(month) {
  const match = String(month || "").match(/^([0-9]{4})-([0-9]{2})$/);
  if (!match) {
    return ["", ""];
  }
  return [match[1], match[2]];
}

function renderBatchActions(batch) {
  if (!batch.workbook_path) {
    return "";
  }
  const pdfButton = batch.pdf_path
    ? `<button class="small-btn open-file" data-path="${escapeAttr(batch.pdf_path)}">打开发票PDF</button>`
    : "";
  return `<button class="small-btn open-file" data-path="${escapeAttr(batch.workbook_path)}">打开报销表</button>${pdfButton}<span class="path">${escapeHtml(batch.workbook_path)}</span>`;
}

function formatBatchLabel(batch, index) {
  const time = formatDateTime(batch.reimbursed_at);
  return `第 ${index + 1} 批${time ? ` ${time}` : ""}`;
}

function formatDateTime(value) {
  if (!value) {
    return "";
  }
  const match = String(value).match(/^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2})/);
  if (!match) {
    return "";
  }
  return `${match[2]}-${match[3]} ${match[4]}:${match[5]}`;
}

function setupInvoiceJobForm() {
  const form = document.getElementById("invoiceJobForm");
  if (!form) {
    return;
  }
  const selectButton = document.getElementById("selectInvoiceDir");
  selectButton.addEventListener("click", async () => {
    selectButton.disabled = true;
    setInvoiceJobStatus("正在打开目录选择窗口。", "running");
    try {
      const res = await fetch("/api/select-invoice-dir", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ default_dir: document.getElementById("invoiceDir").value })
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.error || "选择目录失败");
      }
      if (data.cancelled) {
        setInvoiceJobStatus("已取消选择目录。", "no_new");
      } else {
        setSelectedInvoiceDir(data.invoice_dir || "");
        setInvoiceJobStatus("目录已选择，可以处理并登记。", "success");
      }
    } catch (error) {
      setInvoiceJobStatus(error.message || "选择目录失败", "error");
    } finally {
      selectButton.disabled = false;
    }
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const invoiceDir = document.getElementById("invoiceDir").value.trim();
    const button = document.getElementById("startInvoiceJob");
    if (!invoiceDir) {
      setInvoiceJobStatus("请先填写新报销发票目录。", "error");
      return;
    }
    button.disabled = true;
    setInvoiceJobStatus("已提交，正在启动报销智能体。", "running");
    try {
      const res = await fetch("/api/invoice-jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          invoice_dir: invoiceDir
        })
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.error || "提交失败");
      }
      activeInvoiceJobId = data.id;
      renderInvoiceJob(data);
      pollInvoiceJob();
    } catch (error) {
      button.disabled = false;
      setInvoiceJobStatus(error.message || "提交失败", "error");
    }
  });
}

function setSelectedInvoiceDir(value) {
  const input = document.getElementById("invoiceDir");
  const display = document.getElementById("invoiceDirDisplay");
  input.value = value || "";
  display.textContent = value || "未选择";
  display.classList.toggle("empty", !value);
}

async function pollInvoiceJob() {
  if (!activeInvoiceJobId) {
    return;
  }
  clearTimeout(invoiceJobTimer);
  try {
    const res = await fetch(`/api/invoice-jobs/${activeInvoiceJobId}`);
    const job = await res.json();
    if (!res.ok) {
      throw new Error(job.error || "读取任务状态失败");
    }
    renderInvoiceJob(job);
    if (["queued", "running"].includes(job.status)) {
      invoiceJobTimer = window.setTimeout(pollInvoiceJob, 1800);
      return;
    }
    document.getElementById("startInvoiceJob").disabled = false;
    if (["success", "no_new"].includes(job.status)) {
      await loadFinance();
    }
  } catch (error) {
    document.getElementById("startInvoiceJob").disabled = false;
    setInvoiceJobStatus(error.message || "读取任务状态失败", "error");
  }
}

function renderInvoiceJob(job) {
  const status = job.status || "queued";
  const label = {
    queued: "排队中",
    running: "处理中",
    success: "已完成",
    no_new: "无新凭证",
    error: "处理失败"
  }[status] || status;
  const output = job.output || {};
  const parts = [`${label}：${escapeHtml(job.message || "")}`];
  if (output.total_amount != null) {
    parts.push(`金额 ${money.format(output.total_amount)}`);
  }
  if (output.file_count != null) {
    parts.push(`凭证 ${output.file_count} 张`);
  }
  const links = [];
  if (output.workbook_path) {
    links.push(`<button class="small-btn open-generated" data-path="${escapeAttr(output.workbook_path)}">打开报销表</button>`);
  }
  if (output.pdf_path) {
    links.push(`<button class="small-btn open-generated" data-path="${escapeAttr(output.pdf_path)}">打开发票PDF</button>`);
  }
  setInvoiceJobStatus(`${parts.join("，")}${links.length ? `<span class="job-links">${links.join("")}</span>` : ""}`, status);
  document.querySelectorAll(".open-generated").forEach(button => {
    button.addEventListener("click", async () => {
      await fetch("/api/open-file", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: button.dataset.path })
      });
    });
  });
}

function setInvoiceJobStatus(message, state) {
  const el = document.getElementById("invoiceJobStatus");
  el.className = `job-status ${state || ""}`;
  el.innerHTML = message;
}

setupInvoiceJobForm();
setupMonthFilter();
loadFinance();

function escapeAttr(value) {
  return String(value).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}
