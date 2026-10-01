/**
 * EFDW Frontend — app.js
 * 企业财务实证数据仓库系统 — All API calls, chart rendering, and tab navigation logic.
 * Connects to FastAPI backend at localhost:8000.
 */

const API = 'http://127.0.0.1:8000';

// ─── Utility ───
function $(sel)  { return document.querySelector(sel); }
function $$(sel) { return document.querySelectorAll(sel); }
function fmt(n, d = 4) { return n == null ? '—' : Number(n).toFixed(d); }
function fmtInt(n) { return n == null ? '—' : Number(n).toLocaleString(); }
function fmtBig(n) {
    if (n == null) return '—';
    const v = Number(n);
    if (Math.abs(v) >= 1e8) return (v / 1e8).toFixed(2) + ' 亿';
    if (Math.abs(v) >= 1e4) return (v / 1e4).toFixed(2) + ' 万';
    return v.toFixed(2);
}

// Chart.js global defaults — research-clean
Chart.defaults.font.family = "'Inter', sans-serif";
Chart.defaults.font.size = 11;
Chart.defaults.color = '#64748B';
Chart.defaults.plugins.legend.display = false;
Chart.defaults.plugins.tooltip.backgroundColor = '#111827';
Chart.defaults.plugins.tooltip.cornerRadius = 3;
Chart.defaults.plugins.tooltip.padding = 8;
Chart.defaults.plugins.tooltip.titleFont = { size: 11, weight: '500' };
Chart.defaults.plugins.tooltip.bodyFont = { size: 11 };

// ─── Tab Navigation ───
$$('.nav-tab').forEach(tab => {
    tab.addEventListener('click', () => {
        $$('.nav-tab').forEach(t => t.classList.remove('active'));
        $$('.tab-panel').forEach(p => p.classList.remove('active'));
        tab.classList.add('active');
        $(`#panel-${tab.dataset.tab}`).classList.add('active');
        if (tab.dataset.tab === 'import') {
            loadInventory();
        }
    });
});

// Chart instances (for cleanup)
let chartIndustry = null;
let chartDist = null;
let chartCompanyTrend = null;

// ─── API Status Check ───
async function checkApi() {
    const dot  = $('#api-status .status-dot');
    const text = $('#api-status .status-text');
    try {
        const res = await fetch(`${API}/api/stats`);
        if (res.ok) {
            dot.classList.add('connected');
            dot.classList.remove('error');
            text.textContent = 'API Connected';
            return true;
        }
    } catch (_) {}
    dot.classList.add('error');
    dot.classList.remove('connected');
    text.textContent = 'API Offline';
    return false;
}

// ─── 1. Load Overview Stats ───
async function loadStats() {
    try {
        const data = await (await fetch(`${API}/api/stats`)).json();
        $('#stat-firms').textContent = data.n_firms;
        $('#stat-years').textContent = `${data.year_min}–${data.year_max}`;
        $('#stat-obs').textContent = data.n_obs;
        $('#stat-industries').textContent = data.n_industries;
    } catch (_) {}
}

// ─── 2. Load Descriptive Statistics Table ───
async function loadDescriptive() {
    try {
        const data = await (await fetch(`${API}/api/descriptive?winsorize_pct=0.01`)).json();
        const tbody = $('#desc-tbody');
        tbody.innerHTML = '';
        data.forEach(row => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td>${row.label}</td>
                <td>${row.n}</td>
                <td>${fmt(row.mean)}</td>
                <td>${fmt(row.sd)}</td>
                <td>${fmt(row.min)}</td>
                <td>${fmt(row.p25)}</td>
                <td>${fmt(row.median)}</td>
                <td>${fmt(row.p75)}</td>
                <td>${fmt(row.max)}</td>
            `;
            tbody.appendChild(tr);
        });
    } catch (_) {}
}

// ─── 3. Industry Distribution Chart ───
async function loadIndustryChart() {
    try {
        const data = await (await fetch(`${API}/api/industry`)).json();
        const ctx = $('#chart-industry').getContext('2d');
        if (chartIndustry) chartIndustry.destroy();
        chartIndustry = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: data.map(d => d.industry),
                datasets: [{
                    data: data.map(d => d.n_firms),
                    backgroundColor: '#1E3A5F',
                    borderRadius: 2,
                    barPercentage: 0.5,
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    x: {
                        grid: { display: false },
                        ticks: { font: { size: 11 } }
                    },
                    y: {
                        grid: { color: '#F0F0F0' },
                        ticks: { precision: 0 },
                        title: { display: true, text: '企业数', font: { size: 11 } }
                    }
                }
            }
        });
    } catch (_) {}
}

// ─── 4. Variable Distribution Chart ───
async function loadDistChart(variable = 'roa') {
    try {
        const data = await (await fetch(`${API}/api/distribution?variable=${variable}&winsorize_pct=0.01`)).json();
        const ctx = $('#chart-distribution').getContext('2d');
        if (chartDist) chartDist.destroy();

        const labels = data.bins.map(b => ((b.x0 + b.x1) / 2).toFixed(3));
        const counts = data.bins.map(b => b.count);

        chartDist = new Chart(ctx, {
            type: 'bar',
            data: {
                labels,
                datasets: [{
                    data: counts,
                    backgroundColor: 'rgba(30, 58, 95, 0.65)',
                    borderColor: 'rgba(30, 58, 95, 0.9)',
                    borderWidth: 1,
                    borderRadius: 1,
                    barPercentage: 1.0,
                    categoryPercentage: 1.0,
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    x: {
                        grid: { display: false },
                        ticks: {
                            maxTicksLimit: 8,
                            font: { family: "'JetBrains Mono', monospace", size: 10 }
                        }
                    },
                    y: {
                        grid: { color: '#F0F0F0' },
                        ticks: { precision: 0 },
                        title: { display: true, text: 'Frequency', font: { size: 11 } }
                    }
                }
            }
        });

        $('#dist-summary').textContent = `Mean = ${fmt(data.mean)}    Median = ${fmt(data.median)}    S.D. = ${fmt(data.sd)}`;
    } catch (_) {}
}

// Dist variable selector
$('#dist-var-select').addEventListener('change', (e) => {
    loadDistChart(e.target.value);
});

// ─── 5. Company Search ───
let allCompanies = [];

async function loadCompanies() {
    try {
        allCompanies = await (await fetch(`${API}/api/companies`)).json();
    } catch (_) {}
}

function renderCompanyList(filter = '') {
    const wrap  = $('#company-list-wrap');
    const tbody = $('#company-list-tbody');
    const q = filter.trim().toLowerCase();

    let results = allCompanies;
    if (q) {
        results = allCompanies.filter(c =>
            c.stkcd.includes(q) || c.company_name.toLowerCase().includes(q)
        );
    }
    if (results.length === 0) {
        wrap.style.display = 'none';
        return;
    }
    wrap.style.display = 'block';
    tbody.innerHTML = '';
    results.forEach(c => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td style="font-family:var(--font-mono);">${c.stkcd}</td>
            <td>${c.company_name}</td>
            <td>${c.industry_category}</td>
            <td><button class="btn btn-primary" onclick="loadCompanyDetail('${c.stkcd}')">查看</button></td>
        `;
        tbody.appendChild(tr);
    });
}

$('#company-search-btn').addEventListener('click', () => {
    renderCompanyList($('#company-search-input').value);
});
$('#company-search-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') renderCompanyList($('#company-search-input').value);
});
$('#company-search-input').addEventListener('input', (e) => {
    if (e.target.value.trim() === '') {
        $('#company-list-wrap').style.display = 'none';
    }
});

async function loadCompanyDetail(stkcd) {
    try {
        const data = await (await fetch(`${API}/api/company/${stkcd}`)).json();
        const detail = $('#company-detail');
        detail.style.display = 'block';

        $('#company-name').textContent = data.company_name;
        $('#company-code').textContent = data.stkcd;
        $('#company-industry').textContent = `${data.industry_code} · ${data.industry_category}`;
        $('#company-list-date').textContent = data.list_date ? `上市 ${data.list_date}` : '';

        // Panel table: 核心财务指标时序
        const tbody = $('#company-panel-tbody');
        tbody.innerHTML = '';
        data.panel.forEach(r => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td>${r.year}</td>
                <td>${fmtBig(r.assets)}</td>
                <td>${fmtBig(r.revenue)}</td>
                <td>${fmtBig(r.net_income)}</td>
                <td>${fmt(r.roa, 4)}</td>
                <td>${fmt(r.roe, 4)}</td>
                <td>${fmt(r.revenue_growth, 4)}</td>
                <td>${fmt(r.leverage, 3)}</td>
                <td>${fmt(r.current_ratio, 3)}</td>
            `;
            tbody.appendChild(tr);
        });

        // DuPont 杜邦分解表：ROE = 净利率 × 总资产周转率 × 权益乘数
        const dTbody = $('#dupont-tbody');
        dTbody.innerHTML = '';
        data.panel.forEach(r => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td>${r.year}</td>
                <td>${fmt(r.net_margin, 4)}</td>
                <td>${fmt(r.total_asset_turnover, 3)}</td>
                <td>${fmt(r.equity_multiplier, 3)}</td>
                <td>${fmt(r.dupont_roe, 4)}</td>
            `;
            dTbody.appendChild(tr);
        });

        // Trend chart: ROA vs ROE
        const ctx = $('#chart-company-trend').getContext('2d');
        if (chartCompanyTrend) chartCompanyTrend.destroy();
        chartCompanyTrend = new Chart(ctx, {
            type: 'line',
            data: {
                labels: data.panel.map(r => r.year),
                datasets: [
                    {
                        label: 'ROA',
                        data: data.panel.map(r => r.roa),
                        borderColor: '#1E3A5F',
                        backgroundColor: 'rgba(30,58,95,0.08)',
                        fill: true,
                        tension: 0.3,
                        pointRadius: 3,
                        pointBackgroundColor: '#1E3A5F',
                        borderWidth: 2,
                    },
                    {
                        label: 'ROE',
                        data: data.panel.map(r => r.roe),
                        borderColor: '#B25E00',
                        borderDash: [4, 3],
                        tension: 0.3,
                        pointRadius: 3,
                        pointBackgroundColor: '#B25E00',
                        borderWidth: 1.5,
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: true, position: 'bottom', labels: { boxWidth: 12, font: { size: 11 } } } },
                scales: {
                    x: { grid: { display: false } },
                    y: { grid: { color: '#F0F0F0' } }
                }
            }
        });
    } catch (_) {}
}
// Expose to inline onclick
window.loadCompanyDetail = loadCompanyDetail;

// ─── 6. Export Preview & Download ───
function getExportParams() {
    const yearMin = $('#exp-year-min').value;
    const yearMax = $('#exp-year-max').value;
    const excludeFin = $('#exp-exclude-fin').checked;
    const excludeNew = $('#exp-exclude-new').checked;
    const winPct = $('#exp-winsorize').value;
    const vars = [...$$('#var-checklist input:checked')].map(cb => cb.value).join(',');
    return { yearMin, yearMax, excludeFin, excludeNew, winPct, vars };
}

function buildExportQuery(p) {
    return `year_min=${p.yearMin}&year_max=${p.yearMax}&exclude_financial=${p.excludeFin}&exclude_newly_listed=${p.excludeNew}&winsorize_pct=${p.winPct}&variables=${p.vars}`;
}

$('#btn-preview').addEventListener('click', async () => {
    const p = getExportParams();
    if (!p.vars) {
        $('#preview-info').textContent = '请至少勾选一个导出变量';
        return;
    }
    try {
        // Use descriptive API to get a sense of the data, and fetch CSV for preview
        const qs = buildExportQuery(p);
        const csvRes = await fetch(`${API}/api/export/csv?${qs}`);
        const csvText = await csvRes.text();

        // Parse CSV
        const lines = csvText.trim().split('\n');
        const headers = lines[0].split(',');
        const rows = lines.slice(1, 11).map(l => l.split(','));

        // Render
        $('#preview-thead').innerHTML = '<tr>' + headers.map(h => `<th>${h}</th>`).join('') + '</tr>';
        const tbody = $('#preview-tbody');
        tbody.innerHTML = '';
        rows.forEach(cells => {
            const tr = document.createElement('tr');
            tr.innerHTML = cells.map(c => `<td>${c}</td>`).join('');
            tbody.appendChild(tr);
        });

        $('#preview-info').textContent = `共 ${lines.length - 1} 行 × ${headers.length} 列`;
        $('#preview-table-wrap').style.display = 'block';
        $('#download-row').style.display = 'flex';

        // Set download links
        $('#btn-dl-stata').href = `${API}/api/export/stata?${qs}`;
        $('#btn-dl-csv').href = `${API}/api/export/csv?${qs}`;
    } catch (_) {
        $('#preview-info').textContent = '预览失败，请确认后端 API 正常运行';
    }
});

// ─── Init ───
(async function init() {
    const ok = await checkApi();
    if (!ok) return;
    loadStats();
    loadDescriptive();
    loadIndustryChart();
    loadDistChart('roa');
    loadCompanies();
    loadInventory();
})();


// ─────────────────────────────────────────────
// 7. Load Database Table Inventory
// ─────────────────────────────────────────────
async function loadInventory() {
    const tbody = $('#inventory-tbody');
    try {
        const res = await fetch(`${API}/api/tables`);
        if (!res.ok) throw new Error();
        const tables = await res.json();

        if (tables.length === 0) {
            tbody.innerHTML = `<tr><td colspan="3" class="text-center" style="font-family:var(--font-sans);color:var(--text-muted);text-align:center;">数据库暂无任何数据表，请在上方上传文件导入</td></tr>`;
            return;
        }

        tbody.innerHTML = '';
        tables.forEach(t => {
            let badgeColor = 'var(--text-secondary)';
            let badgeBg = 'var(--bg)';
            let layerName = '未知层';

            if (t.schema === 'ods') {
                layerName = '贴源层 (ODS)';
                badgeColor = '#B25E00';
                badgeBg = '#FFF5E6';
            } else if (t.schema === 'dwd') {
                layerName = '明细层 (DWD)';
                badgeColor = '#059669';
                badgeBg = '#E6F4EA';
            } else if (t.schema === 'dws') {
                layerName = '服务层 (DWS)';
                badgeColor = 'var(--accent)';
                badgeBg = 'var(--accent-bg)';
            } else if (t.schema === 'ads') {
                layerName = '应用层 (ADS)';
                badgeColor = '#7C3AED';
                badgeBg = '#F3E8FF';
            }

            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td style="font-family:var(--font-mono); font-weight:600; text-align:left; color:var(--text-primary);">${t.full_name}</td>
                <td style="text-align:center;"><span style="font-size:0.7rem; font-weight:600; padding:2px 6px; border-radius:3px; color:${badgeColor}; background:${badgeBg}">${layerName}</span></td>
                <td style="font-family:var(--font-mono); font-weight:500; color:var(--text-secondary); text-align:right;">${fmtInt(t.rows)}</td>
            `;
            tbody.appendChild(tr);
        });
    } catch (_) {
        tbody.innerHTML = `<tr><td colspan="3" class="text-center" style="font-family:var(--font-sans);color:var(--red);text-align:center;">无法获取数据库结构，请确保 API 正常运行</td></tr>`;
    }
}


// ─────────────────────────────────────────────
// 8. Drag and Drop File Upload
// ─────────────────────────────────────────────
const dropzone = $('#upload-dropzone');
const fileInput = $('#import-file-input');
const btnImport = $('#btn-trigger-import');
let selectedFile = null;

// Click dropzone to select file
dropzone.addEventListener('click', () => fileInput.click());

// File input selection
fileInput.addEventListener('change', (e) => {
    if (e.target.files.length > 0) {
        handleFileSelection(e.target.files[0]);
    }
});

// Drag and drop event handlers
dropzone.addEventListener('dragover', (e) => {
    e.preventDefault();
    dropzone.classList.add('dragover');
});
dropzone.addEventListener('dragleave', () => {
    dropzone.classList.remove('dragover');
});
dropzone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropzone.classList.remove('dragover');
    if (e.dataTransfer.files.length > 0) {
        handleFileSelection(e.dataTransfer.files[0]);
    }
});

function handleFileSelection(file) {
    selectedFile = file;
    dropzone.classList.add('file-selected');

    // Update dropzone UI
    const icon = dropzone.querySelector('.dropzone-icon');
    const text = dropzone.querySelector('.dropzone-text');
    const sub = dropzone.querySelector('.dropzone-sub');

    icon.textContent = '📄';
    text.innerHTML = `已选择文件: <span style="color:var(--accent-light); font-weight:600;">${file.name}</span>`;
    sub.textContent = `文件大小: ${(file.size / 1024 / 1024).toFixed(2)} MB | 点击或拖拽更换文件`;

    // Auto-populate target table name if empty
    const tableNameInput = $('#import-table-name');
    if (!tableNameInput.value || tableNameInput.value.startsWith('ods.')) {
        // Clean name to ods.filename
        const stem = file.name.substring(0, file.name.lastIndexOf('.')).toLowerCase().replace(/[^a-z0-9_]/g, '_').replace(/__+/g, '_').replace(/^_+|_+$/g, '');
        tableNameInput.value = `ods.${stem}`;
    }

    btnImport.removeAttribute('disabled');
}


// ─────────────────────────────────────────────
// 9. File Upload API Submission
// ─────────────────────────────────────────────
btnImport.addEventListener('click', async () => {
    if (!selectedFile) return;

    const tableName = $('#import-table-name').value.trim();
    const varcharCols = $('#import-varchar-cols').value.trim();

    // UI state
    btnImport.setAttribute('disabled', 'true');
    btnImport.innerHTML = `⏳ 正在导入中...`;

    const placeholder = $('#import-result-placeholder');
    const report = $('#import-result-report');

    placeholder.style.display = 'none';
    report.style.display = 'block';

    const alertBox = $('#import-report-alert');
    alertBox.style.display = 'flex';
    alertBox.className = 'alert-box loading';
    alertBox.innerHTML = `<span>⏳</span> 正在将文件导入并进行数据质量预检，请稍候...`;

    const formData = new FormData();
    formData.append('file', selectedFile);
    formData.append('table_name', tableName);
    formData.append('varchar_columns', varcharCols);

    try {
        const res = await fetch(`${API}/api/import`, {
            method: 'POST',
            body: formData
        });

        if (!res.ok) {
            const errData = await res.json();
            throw new Error(errData.detail || '导入失败，请检查文件格式。');
        }

        const data = await res.json();

        // Success Alert
        alertBox.className = 'alert-box success';
        alertBox.innerHTML = `<span>✅</span> <div><strong>导入成功！</strong> ${data.message}</div>`;

        // Populate Summary
        $('#q-table-name').textContent = data.import_details.table;
        $('#q-total-rows').textContent = fmtInt(data.quality.total_rows);

        const dupRows = data.quality.duplicate_rows;
        const dupEl = $('#q-dup-rows');
        dupEl.textContent = fmtInt(dupRows);
        if (dupRows > 0) {
            dupEl.style.color = 'var(--red)';
            alertBox.innerHTML += `<div style="margin-top:4px;">⚠️ 检测到该表中存在 <strong>${fmtInt(dupRows)}</strong> 行完全重复的数据，可能会破坏面板分析的主键唯一性，请核实数据源。</div>`;
        } else {
            dupEl.style.color = 'var(--green)';
        }

        // Populate Quality Details
        const qTbody = $('#quality-tbody');
        qTbody.innerHTML = '';
        data.quality.columns.forEach(col => {
            const tr = document.createElement('tr');

            let nullColor = 'var(--text-primary)';
            if (col.null_pct > 50) nullColor = 'var(--red)';
            else if (col.null_pct > 0) nullColor = 'var(--text-secondary)';

            tr.innerHTML = `
                <td style="text-align:left; font-weight:500;">${col.name}</td>
                <td style="font-family:var(--font-mono); text-align:center;">${col.dtype}</td>
                <td style="font-family:var(--font-mono);">${fmtInt(col.null_count)}</td>
                <td style="font-family:var(--font-mono); font-weight:600; color:${nullColor}">${col.null_pct.toFixed(1)}%</td>
            `;
            qTbody.appendChild(tr);
        });

        // Populate Preview Table
        const pThead = $('#import-preview-thead');
        const pTbody = $('#import-preview-tbody');

        pThead.innerHTML = '<tr>' + data.preview.columns.map(h => `<th>${h}</th>`).join('') + '</tr>';

        pTbody.innerHTML = '';
        data.preview.data.forEach(row => {
            const tr = document.createElement('tr');
            tr.innerHTML = row.map(val => {
                const displayVal = val === null ? '<span style="color:var(--text-muted);font-style:italic;">null</span>' : val;
                return `<td>${displayVal}</td>`;
            }).join('');
            pTbody.appendChild(tr);
        });

        // Refresh local database inventory
        loadInventory();

    } catch (err) {
        alertBox.className = 'alert-box error';
        alertBox.innerHTML = `<span>❌</span> <div><strong>导入失败！</strong> 发生错误: ${err.message}</div>`;

        // Reset report card
        $('#q-table-name').textContent = '—';
        $('#q-total-rows').textContent = '—';
        $('#q-dup-rows').textContent = '—';
        $('#quality-tbody').innerHTML = '';
        $('#import-preview-thead').innerHTML = '';
        $('#import-preview-tbody').innerHTML = '';
    } finally {
        btnImport.removeAttribute('disabled');
        btnImport.innerHTML = `🚀 开始导入 DuckDB`;
    }
});


// ─────────────────────────────────────────────
// 10. Pipeline Runner Trigger
// ─────────────────────────────────────────────
const btnRunPipeline = $('#btn-run-pipeline');
const pipelineAlert = $('#pipeline-status-alert');

btnRunPipeline.addEventListener('click', async () => {
    btnRunPipeline.setAttribute('disabled', 'true');
    btnRunPipeline.innerHTML = `⏳ Pipeline 正在执行洗数...`;

    pipelineAlert.style.display = 'flex';
    pipelineAlert.className = 'alert-box loading';
    pipelineAlert.innerHTML = `<span>⚙️</span> <div>DuckDB 正在执行数据分层清洗、多源实体关联、财务指标聚合与多期滞后项计算，请耐心等候...</div>`;

    try {
        const res = await fetch(`${API}/api/run-pipeline`, {
            method: 'POST'
        });

        if (!res.ok) {
            const errData = await res.json();
            throw new Error(errData.detail || '数仓 Pipeline 运行失败。');
        }

        const data = await res.json();

        pipelineAlert.className = 'alert-box success';
        pipelineAlert.innerHTML = `<span>✅</span> <div><strong>数仓清洗成功！</strong> DWD 维度表、DWS 财务指标面板与 ADS 行业聚合集市已重构！${data.message}</div>`;

        // Trigger generic UI refreshes to pull in the new data
        await checkApi();
        await loadStats();
        await loadDescriptive();
        await loadIndustryChart();
        await loadDistChart('roa');
        await loadCompanies();
        await loadInventory();

    } catch (err) {
        pipelineAlert.className = 'alert-box error';
        pipelineAlert.innerHTML = `<span>❌</span> <div><strong>执行失败！</strong> 发生错误: ${err.message}</div>`;
    } finally {
        btnRunPipeline.removeAttribute('disabled');
        btnRunPipeline.innerHTML = `⚙️ 运行清洗 Pipeline`;
    }
});


// ─────────────────────────────────────────────
// 11. Smart Entity Resolution & Alignment
// ─────────────────────────────────────────────
const thresholdSlider = $('#align-threshold');
const thresholdVal = $('#align-threshold-val');

if (thresholdSlider && thresholdVal) {
    thresholdSlider.addEventListener('input', (e) => {
        thresholdVal.textContent = Number(e.target.value).toFixed(2);
    });
}

const btnRunAlign = $('#btn-run-align');
let alignedResultsCache = null; // To cache results for CSV download

if (btnRunAlign) {
    btnRunAlign.addEventListener('click', async () => {
        const namesInput = $('#align-names-input').value;
        const threshold = parseFloat(thresholdSlider.value);

        // Parse names
        const names = namesInput.split(/[\n,]/).map(n => n.trim()).filter(n => n !== '');

        if (names.length === 0) {
            alert('请在文本框中输入至少一个企业名称。');
            return;
        }

        // UI states
        btnRunAlign.setAttribute('disabled', 'true');
        btnRunAlign.innerHTML = `⏳ 正在实体对齐中...`;

        const emptyState = $('#align-empty-state');
        const summaryRow = $('#align-summary-row');
        const resultsBlock = $('#align-results-block');

        try {
            const res = await fetch(`${API}/api/resolve`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ names, threshold })
            });

            if (!res.ok) throw new Error('接口对齐失败，请检查后端 API');

            const data = await res.json();
            alignedResultsCache = data; // Cache

            // Calculate stats
            const total = data.length;
            const matchedCount = data.filter(d => d.match_type !== 'No Match').length;
            const exactCount = data.filter(d => d.match_type === 'Current Exact Match' || d.match_type === 'Historical Exact Match').length;
            const successRate = total > 0 ? (matchedCount / total * 100).toFixed(1) + '%' : '0%';

            // Show / Hide blocks
            emptyState.style.display = 'none';
            summaryRow.style.display = 'grid';
            resultsBlock.style.display = 'block';

            // Update stats cards
            $('#align-stat-total').textContent = total;
            $('#align-stat-success').textContent = successRate;
            $('#align-stat-exact').textContent = exactCount;

            // Render Table
            const tbody = $('#align-results-tbody');
            tbody.innerHTML = '';

            data.forEach(row => {
                let badgeClass = 'badge-nomatch';
                let levelText = '未匹配';

                if (row.match_type === 'Current Exact Match') {
                    badgeClass = 'badge-current';
                    levelText = '官方精准';
                } else if (row.match_type === 'Historical Exact Match') {
                    badgeClass = 'badge-historical';
                    levelText = '曾用名精准';
                } else if (row.match_type === 'Substring Match') {
                    badgeClass = 'badge-substring';
                    levelText = '子串包含';
                } else if (row.match_type === 'Fuzzy Match') {
                    badgeClass = 'badge-fuzzy';
                    levelText = '模糊匹配';
                }

                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td style="text-align:left; font-weight:500; font-family:var(--font-sans); color:var(--text-secondary);">${row.raw_name}</td>
                    <td style="font-family:var(--font-mono); font-weight:600;">${row.stkcd}</td>
                    <td style="font-family:var(--font-sans); color:var(--text-primary); font-weight:500;">${row.company_name}</td>
                    <td style="font-family:var(--font-sans);">${row.industry_category}</td>
                    <td style="text-align:center;"><span class="badge-match ${badgeClass}">${levelText}</span></td>
                    <td style="font-family:var(--font-mono); font-weight:600; color:var(--text-secondary);">${row.score.toFixed(3)}</td>
                `;
                tbody.appendChild(tr);
            });

        } catch (err) {
            alert('发生错误: ' + err.message);
            emptyState.style.display = 'flex';
            summaryRow.style.display = 'none';
            resultsBlock.style.display = 'none';
        } finally {
            btnRunAlign.removeAttribute('disabled');
            btnRunAlign.innerHTML = `🔍 智能对齐与身份消歧`;
        }
    });
}

// Export Aligned Mapping CSV
const btnDlAlignCsv = $('#btn-dl-align-csv');

if (btnDlAlignCsv) {
    btnDlAlignCsv.addEventListener('click', (e) => {
        e.preventDefault();
        if (!alignedResultsCache || alignedResultsCache.length === 0) return;

        // Build CSV content
        let csvContent = '﻿'; // BOM to prevent garbled characters in Excel
        csvContent += '原始输入名称,标准股票代码,标准公司名称,所属行业,对齐级别,匹配分数\n';

        alignedResultsCache.forEach(row => {
            let levelText = '未匹配';
            if (row.match_type === 'Current Exact Match') levelText = '官方精准';
            else if (row.match_type === 'Historical Exact Match') levelText = '曾用名精准';
            else if (row.match_type === 'Substring Match') levelText = '子串包含';
            else if (row.match_type === 'Fuzzy Match') levelText = '模糊匹配';

            const rawName = row.raw_name.replace(/"/g, '""');
            const compName = row.company_name.replace(/"/g, '""');

            csvContent += `"${rawName}","${row.stkcd}","${compName}","${row.industry_category}","${levelText}",${row.score}\n`;
        });

        // Trigger download
        const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.setAttribute('href', url);
        link.setAttribute('download', 'corporate_entity_alignment_report.csv');
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
    });
}
