/**
 * EFDW Frontend — app.js
 * 企业财务实证数据仓库系统 — All API calls, chart rendering, and tab navigation logic.
 * Connects to FastAPI backend at localhost:8000.
 */

const API = location.protocol === 'file:' ? 'http://127.0.0.1:8000' : '';

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
let dictLoaded = false;

$$('.nav-tab').forEach(tab => {
    tab.addEventListener('click', () => {
        $$('.nav-tab').forEach(t => t.classList.remove('active'));
        $$('.tab-panel').forEach(p => p.classList.remove('active'));
        tab.classList.add('active');
        $(`#panel-${tab.dataset.tab}`).classList.add('active');
        if (tab.dataset.tab === 'import') {
            loadInventory();
            loadQuality();
        }
        if (tab.dataset.tab === 'dict' && !dictLoaded) {
            loadDict();
        }
    });
});

// Chart instances (for cleanup)
let chartIndustry = null;
let chartDist = null;
let chartCompanyTrend = null;

// ─── 中文友好错误映射 ───
// 把原始网络/HTTP 错误转换为中文提示 + 解决建议
async function fetchJson(url, options) {
    let res;
    try {
        res = await fetch(url, options);
    } catch (_) {
        throw new Error('看板服务未启动或已断线：请双击 启动看板.bat（或在项目根目录运行 uvicorn src.api:app --port 8000）后刷新页面。');
    }
    if (!res.ok) {
        let detail = '';
        try { detail = (await res.json()).detail || ''; } catch (_) {}
        if (/lock|锁定|占用|重建/i.test(detail)) {
            throw new Error('数据库正在重建中或被其他任务占用，请稍后重试。');
        }
        if (res.status === 404) throw new Error(detail || '请求的资源不存在（404），请检查输入是否正确。');
        if (res.status === 503) throw new Error(detail || '数据库未初始化，请先按页面上方的新手引导完成数据装载。');
        if (res.status === 500) {
            throw new Error(detail ? `${detail}（可查看 logs/efdw.log 排查）` : '服务器内部错误（500），可查看 logs/efdw.log 排查。');
        }
        throw new Error(detail || `请求失败（HTTP ${res.status}）。`);
    }
    return res.json();
}

// ─── API Status Check ───
// 返回 'ok'（数据就绪）/ 'offline'（服务未启动）/ 'missing'（库未初始化）/ 'empty'（库为空无样本）
async function checkApi() {
    const dot  = $('#api-status .status-dot');
    const text = $('#api-status .status-text');
    try {
        const res = await fetch(`${API}/api/stats`);
        if (res.ok) {
            const data = await res.json();
            if (!data.n_firms || !data.n_obs) {
                dot.classList.add('error');
                dot.classList.remove('connected');
                text.textContent = '数据库为空';
                return 'empty';
            }
            dot.classList.add('connected');
            dot.classList.remove('error');
            text.textContent = 'API Connected';
            return 'ok';
        }
        if (res.status === 503) {
            dot.classList.add('error');
            dot.classList.remove('connected');
            text.textContent = '数据库未初始化';
            return 'missing';
        }
    } catch (_) {}
    dot.classList.add('error');
    dot.classList.remove('connected');
    text.textContent = 'API Offline';
    return 'offline';
}

// ─── 空库 / 服务未就绪 新手引导 ───
function showOnboarding(status) {
    const card = $('#onboarding-card');
    const lead = $('#onboarding-lead');
    const steps = $('#onboarding-steps');
    const cmd = $('#onboarding-cmd');
    card.style.display = 'block';
    if (status === 'offline') {
        // 服务没起：与"库为空"区分提示
        lead.textContent = '看板服务未启动或已断线：请双击项目根目录的 启动看板.bat（或在项目根目录运行 uvicorn src.api:app --port 8000），待服务启动后刷新本页面。';
        steps.style.display = 'none';
        cmd.style.display = 'none';
    } else {
        lead.textContent = status === 'empty'
            ? '数据库已建立，但里面还没有任何样本数据。按下面三步完成首次数据装载，即可开始分析：'
            : '数据库尚未初始化。按下面三步完成首次安装，即可开始分析：';
        steps.style.display = '';
        cmd.style.display = '';
    }
}

function hideOnboarding() {
    $('#onboarding-card').style.display = 'none';
}

// ─── 1. Load Overview Stats ───
let exportYearsInited = false;  // 导出页年份默认值仅初始化一次，避免覆盖用户手改的值

async function loadStats() {
    try {
        const data = await (await fetch(`${API}/api/stats`)).json();
        $('#stat-firms').textContent = data.n_firms;
        $('#stat-years').textContent = `${data.year_min}–${data.year_max}`;
        $('#stat-obs').textContent = data.n_obs;
        $('#stat-industries').textContent = data.n_industries;
        // 页头展示"数据更新至 YYYY 年 / 库更新时间"
        const info = $('#header-data-info');
        if (info && data.db_updated_at) {
            info.textContent = `数据更新至 ${data.year_max} 年 · 库更新 ${data.db_updated_at}`;
        }
        // 数据导出页年份窗口默认值 = 库内实际年份范围（仅首次加载填充）
        if (!exportYearsInited) {
            const yMin = $('#exp-year-min');
            const yMax = $('#exp-year-max');
            if (yMin && yMax) {
                yMin.value = data.year_min;
                yMax.value = data.year_max;
                exportYearsInited = true;
            }
        }
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

function showCompanyEmpty(msg) {
    const empty = $('#company-empty-state');
    empty.innerHTML = msg;
    empty.style.display = msg ? 'block' : 'none';
}

async function renderCompanyList(filter = '') {
    const wrap  = $('#company-list-wrap');
    const tbody = $('#company-list-tbody');
    const q = filter.trim().toLowerCase();

    // 每次发起新搜索，先隐藏上一个公司的详情面板，防止旧数据残留被误读
    $('#company-detail').style.display = 'none';

    // 空查询：收起结果区，不做全量罗列
    if (!q) {
        wrap.style.display = 'none';
        showCompanyEmpty('');
        return;
    }

    // 企业清单尚未加载完成时给出简单加载态
    if (allCompanies.length === 0) {
        showCompanyEmpty('⏳ 企业清单加载中，请稍候…');
        await loadCompanies();
    }

    const results = allCompanies.filter(c =>
        c.stkcd.includes(q) || (c.company_name || '').toLowerCase().includes(q)
    );

    // 无匹配：显示明确的空状态提示，且不残留任何旧结果/旧详情
    if (results.length === 0) {
        wrap.style.display = 'none';
        tbody.innerHTML = '';
        showCompanyEmpty('🔍 未找到匹配的企业，请检查代码或名称是否正确');
        return;
    }

    showCompanyEmpty('');
    wrap.style.display = 'block';
    tbody.innerHTML = '';
    results.forEach(c => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td style="font-family:var(--font-mono);">${c.stkcd}</td>
            <td>${c.company_name || '—'}</td>
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
        showCompanyEmpty('');
    }
});

function riskBadgeHTML(risk) {
    if (risk === '危险区') return '<span class="risk-badge risk-danger">危险区</span>';
    if (risk === '灰色区') return '<span class="risk-badge risk-gray">灰色区</span>';
    if (risk === '安全区') return '<span class="risk-badge risk-safe">安全区</span>';
    return '—';
}

async function loadCompanyDetail(stkcd) {
    try {
        const data = await (await fetch(`${API}/api/company/${stkcd}`)).json();
        const detail = $('#company-detail');
        detail.style.display = 'block';

        $('#company-name').textContent = data.company_name;
        $('#company-code').textContent = data.stkcd;
        $('#company-industry').textContent = `${data.industry_code} · ${data.industry_category}`;
        $('#company-list-date').textContent = data.list_date ? `上市 ${data.list_date}` : '';

        // Panel table: 核心财务指标时序（含 Altman Z 预警）
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
                <td>${r.altman_z == null ? '—' : fmt(r.altman_z, 3)}</td>
                <td>${riskBadgeHTML(r.altman_risk)}</td>
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

        // 行业对标分析面板
        loadBenchmark(stkcd);
    } catch (_) {
        // 详情加载失败：隐藏可能残留的旧面板并给出提示
        $('#company-detail').style.display = 'none';
        showCompanyEmpty('⚠️ 企业详情加载失败，请确认 API 正常运行后重试');
    }
}
// Expose to inline onclick
window.loadCompanyDetail = loadCompanyDetail;

// ─── 5b. Industry Benchmarking（行业对标分析） ───
let benchmarkCache = null;

async function loadBenchmark(stkcd) {
    const tbody = $('#benchmark-tbody');
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; color:var(--text-muted);">正在加载行业对标数据...</td></tr>';
    try {
        const res = await fetch(`${API}/api/benchmark/${stkcd}`);
        if (!res.ok) throw new Error();
        benchmarkCache = await res.json();
        const years = benchmarkCache.indicators.length ? benchmarkCache.indicators[0].values.map(v => v.year) : [];
        const sel = $('#benchmark-year-select');
        sel.innerHTML = years.map(y => `<option value="${y}">${y} 年</option>`).join('');
        sel.value = years[years.length - 1];
        renderBenchmark(years[years.length - 1]);
    } catch (_) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; color:var(--red);">行业对标数据加载失败</td></tr>';
    }
}

function renderBenchmark(year) {
    if (!benchmarkCache) return;
    const tbody = $('#benchmark-tbody');
    tbody.innerHTML = '';
    benchmarkCache.indicators.forEach(ind => {
        const v = ind.values.find(x => x.year === Number(year));
        const tr = document.createElement('tr');
        if (!v || v.company === null || v.industry_median === null) {
            tr.innerHTML = `<td style="text-align:left;">${ind.label}</td><td colspan="5" style="text-align:center; color:var(--text-muted);">该企业本年度不适用（如金融业的 Altman Z）</td>`;
            tbody.appendChild(tr);
            return;
        }
        const gap = v.company - v.industry_median;
        const pct = Math.round((v.percentile ?? 0) * 100);
        const isGood = ind.direction === 'lower' ? gap < 0 : gap > 0;
        const gapColor = Math.abs(gap) < 1e-9 ? 'var(--text-secondary)' : (isGood ? 'var(--green)' : 'var(--red)');
        const gapText = (gap > 0 ? '+' : '') + gap.toFixed(4);
        const dirBadge = ind.direction === 'lower'
            ? '<span class="badge-match badge-fuzzy">↓ 越低越优</span>'
            : '<span class="badge-match badge-current">↑ 越高越优</span>';
        tr.innerHTML = `
            <td style="text-align:left;">${ind.label}</td>
            <td style="font-weight:600;">${v.company.toFixed(4)}</td>
            <td>${v.industry_median.toFixed(4)}</td>
            <td style="color:${gapColor}; font-weight:600;">${gapText}</td>
            <td>
                <div class="pct-bar"><div class="pct-fill" style="width:${pct}%"></div></div>
                <span style="font-size:0.72rem; color:var(--text-muted);">${pct}%</span>
            </td>
            <td>${dirBadge}</td>
        `;
        tbody.appendChild(tr);
    });
}

$('#benchmark-year-select').addEventListener('change', (e) => renderBenchmark(Number(e.target.value)));

// ─── 6. Export Preview & Download ───
let lastExportQs = '';  // 最近一次预览使用的查询串（meta 配方文件跟随下载用）

// 勾选"同时下载配方说明"时，数据文件下载后自动跟随下载 _meta.txt
function maybeDownloadMeta() {
    if (!lastExportQs || !$('#exp-include-meta').checked) return;
    setTimeout(() => {
        const a = document.createElement('a');
        a.href = `${API}/api/export/meta?${lastExportQs}`;
        a.download = 'financial_regression_panel_meta.txt';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
    }, 400);
}

$('#btn-dl-stata').addEventListener('click', maybeDownloadMeta);
$('#btn-dl-csv').addEventListener('click', maybeDownloadMeta);

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
        const qs = buildExportQuery(p);
        const csvRes = await fetch(`${API}/api/export/csv?${qs}`);
        if (!csvRes.ok) {
            // 复用统一的中文友好错误映射
            await fetchJson(`${API}/api/export/csv?${qs}`);
        }
        const csvText = await csvRes.text();

        const lines = csvText.trim().split('\n');
        const headers = lines[0].split(',');
        const rows = lines.slice(1, 11).map(l => l.split(','));

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

        lastExportQs = qs;
        $('#btn-dl-stata').href = `${API}/api/export/stata?${qs}`;
        $('#btn-dl-csv').href = `${API}/api/export/csv?${qs}`;
    } catch (err) {
        $('#preview-info').textContent = '预览失败：' + err.message;
    }
});

// ─── 7. Correlation Matrix ───
$('#btn-run-corr').addEventListener('click', async () => {
    const vars = [...$$('#corr-var-checklist input:checked')].map(cb => cb.value);
    if (vars.length < 2) {
        alert('相关矩阵至少需要勾选 2 个变量');
        return;
    }
    const btn = $('#btn-run-corr');
    btn.setAttribute('disabled', 'true');
    btn.textContent = '⏳ 计算中...';
    try {
        const qs = new URLSearchParams({
            exclude_financial: $('#corr-exclude-fin').checked,
            exclude_newly_listed: $('#corr-exclude-new').checked,
            winsorize_pct: $('#corr-winsorize').checked ? 0.01 : 0,
            variables: vars.join(','),
        });
        const data = await fetchJson(`${API}/api/correlation?${qs}`);

        // Render heatmap table
        const table = $('#corr-table');
        const names = data.variables.map(v => v.name);
        let html = '<thead><tr><th class="corr-row-head"></th>';
        names.forEach(n => { html += `<th>${n}</th>`; });
        html += '</tr></thead><tbody>';
        data.matrix.forEach((row, i) => {
            html += `<tr><th class="corr-row-head">${names[i]}</th>`;
            row.forEach(v => {
                if (v === null || v === undefined) {
                    html += '<td class="corr-cell">—</td>';
                } else {
                    const abs = Math.min(Math.abs(v), 1);
                    const bg = v >= 0
                        ? `rgba(30, 58, 95, ${(0.08 + abs * 0.72).toFixed(3)})`
                        : `rgba(178, 94, 0, ${(0.08 + abs * 0.72).toFixed(3)})`;
                    const color = abs > 0.55 ? '#FFFFFF' : 'var(--text-primary)';
                    html += `<td class="corr-cell" style="background:${bg}; color:${color};">${v.toFixed(3)}</td>`;
                }
            });
            html += '</tr>';
        });
        html += '</tbody>';
        table.innerHTML = html;

        $('#corr-note').textContent = `Pearson 相关系数（N = ${data.n}），双侧 1% 缩尾后计算；蓝色 = 正相关，橙色 = 负相关`;
        $('#corr-empty-state').style.display = 'none';
        $('#corr-table-wrap').style.display = 'block';
    } catch (err) {
        alert('相关矩阵计算失败: ' + err.message);
    } finally {
        btn.removeAttribute('disabled');
        btn.textContent = '📈 生成相关矩阵';
    }
});

// ─── 8. Variable Dictionary ───
let dictCache = [];

async function loadDict() {
    const tbody = $('#dict-tbody');
    try {
        const res = await fetch(`${API}/api/dict`);
        if (!res.ok) throw new Error();
        dictCache = await res.json();
        dictLoaded = true;
        renderDict('');
    } catch (_) {
        tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; color:var(--red);">变量字典加载失败，请确认 API 正常运行</td></tr>';
    }
}

function renderDict(filter) {
    const tbody = $('#dict-tbody');
    const q = filter.trim().toLowerCase();
    const rows = q
        ? dictCache.filter(d =>
            d.variable.toLowerCase().includes(q) ||
            d.label.toLowerCase().includes(q) ||
            d.category.toLowerCase().includes(q))
        : dictCache;
    if (rows.length === 0) {
        tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; color:var(--text-muted);">无匹配变量</td></tr>';
        return;
    }
    tbody.innerHTML = '';
    rows.forEach(d => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td style="font-family:var(--font-mono); font-weight:600; color:var(--accent);">${d.variable}</td>
            <td>${d.label}</td>
            <td><span class="badge-match badge-substring">${d.category}</span></td>
            <td style="font-family:var(--font-mono); font-size:0.76rem; color:var(--text-secondary);">${d.formula}</td>
        `;
        tbody.appendChild(tr);
    });
}

$('#dict-search-input').addEventListener('input', (e) => {
    if (!dictLoaded) return;
    renderDict(e.target.value);
});

// ─── Init ───
(async function init() {
    const status = await checkApi();
    // 数据未就绪（服务离线 / 库未初始化 / 库为空）：显示新手引导，不加载分析数据
    if (status !== 'ok') {
        showOnboarding(status);
        return;
    }
    hideOnboarding();
    loadStats();
    loadDescriptive();
    loadIndustryChart();
    loadDistChart('roa');
    loadCompanies();
    loadInventory();
})();


// ─────────────────────────────────────────────
// 9. Load Database Table Inventory
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
// 10. Drag and Drop File Upload
// ─────────────────────────────────────────────
const dropzone = $('#upload-dropzone');
const fileInput = $('#import-file-input');
const btnImport = $('#btn-trigger-import');
let selectedFile = null;

dropzone.addEventListener('click', () => fileInput.click());

fileInput.addEventListener('change', (e) => {
    if (e.target.files.length > 0) {
        handleFileSelection(e.target.files[0]);
    }
});

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

    const icon = dropzone.querySelector('.dropzone-icon');
    const text = dropzone.querySelector('.dropzone-text');
    const sub = dropzone.querySelector('.dropzone-sub');

    icon.textContent = '📄';
    text.innerHTML = `已选择文件: <span style="color:var(--accent-light); font-weight:600;">${file.name}</span>`;
    sub.textContent = `文件大小: ${(file.size / 1024 / 1024).toFixed(2)} MB | 点击或拖拽更换文件`;

    const tableNameInput = $('#import-table-name');
    if (!tableNameInput.value || tableNameInput.value.startsWith('ods.')) {
        const stem = file.name.substring(0, file.name.lastIndexOf('.')).toLowerCase().replace(/[^a-z0-9_]/g, '_').replace(/__+/g, '_').replace(/^_+|_+$/g, '');
        tableNameInput.value = `ods.${stem}`;
    }

    btnImport.removeAttribute('disabled');
}


// ─────────────────────────────────────────────
// 11. File Upload API Submission
// ─────────────────────────────────────────────
btnImport.addEventListener('click', async () => {
    if (!selectedFile) return;

    const tableName = $('#import-table-name').value.trim();
    const varcharCols = $('#import-varchar-cols').value.trim();

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
        const data = await fetchJson(`${API}/api/import`, {
            method: 'POST',
            body: formData
        });

        alertBox.className = 'alert-box success';
        alertBox.innerHTML = `<span>✅</span> <div><strong>导入成功！</strong> ${data.message}</div>`;

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

        loadInventory();

    } catch (err) {
        alertBox.className = 'alert-box error';
        alertBox.innerHTML = `<span>❌</span> <div><strong>导入失败！</strong> 发生错误: ${err.message}</div>`;

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
// 11b. Financial Data Quality Console（财务勾稽校验台）
// ─────────────────────────────────────────────
async function loadQuality() {
    try {
        const res = await fetch(`${API}/api/quality`);
        if (!res.ok) throw new Error();
        renderQuality(await res.json());
    } catch (_) {
        $('#quality-banner').style.display = 'flex';
        $('#quality-banner').className = 'alert-box error';
        $('#quality-banner').innerHTML = '<span>❌</span> <div>质检报告加载失败，请确认 API 正常运行</div>';
    }
}

function renderQuality(report) {
    $('#q-total-checks').textContent = fmtInt(report.total_checks);
    $('#q-passed').textContent = fmtInt(report.passed);
    $('#q-warned').textContent = fmtInt(report.warned);
    $('#q-failed').textContent = fmtInt(report.failed);

    const banner = $('#quality-banner');
    banner.style.display = 'flex';
    if (report.failed === 0 && report.warned === 0) {
        banner.className = 'alert-box success';
        banner.innerHTML = `<span>✅</span> <div><strong>全部通过！</strong> ${report.n_firm_years} 个企业-年度 × ${report.checks_per_firm_year} 项勾稽检查全部满足，通过率 100%。接入真实数据后此处将自动暴露违规明细。</div>`;
        $('#quality-table-wrap').style.display = 'none';
    } else {
        banner.className = 'alert-box error' ;
        banner.innerHTML = `<span>⚠️</span> <div><strong>发现 ${report.failed + report.warned} 项违规</strong>（通过率 ${(report.pass_rate * 100).toFixed(1)}%），明细如下，请核查数据源。</div>`;
        const tbody = $('#quality-tbody');
        tbody.innerHTML = '';
        report.violations.forEach(v => {
            const tr = document.createElement('tr');
            const sevBadge = v.severity === 'FAIL'
                ? '<span class="risk-badge risk-danger">FAIL</span>'
                : '<span class="risk-badge risk-gray">WARN</span>';
            tr.innerHTML = `
                <td style="font-family:var(--font-mono); font-weight:600;">${v.stkcd}</td>
                <td>${v.company_name}</td>
                <td>${v.year}</td>
                <td style="text-align:left;">${v.check_item}</td>
                <td>${sevBadge}</td>
                <td style="text-align:left; font-size:0.76rem;">${v.detail}</td>
            `;
            tbody.appendChild(tr);
        });
        $('#quality-table-wrap').style.display = 'block';
    }
}

const btnRunQuality = $('#btn-run-quality');
if (btnRunQuality) {
    btnRunQuality.addEventListener('click', async () => {
        btnRunQuality.setAttribute('disabled', 'true');
        btnRunQuality.textContent = '⏳ 校验中...';
        try {
            const data = await fetchJson(`${API}/api/run-quality`, { method: 'POST' });
            renderQuality({
                ...data.summary,
                n_firm_years: Math.round(data.summary.total_checks / 10),
                checks_per_firm_year: 10,
                violations: [],
            });
            // 违规明细重新拉取完整报告
            await loadQuality();
        } catch (err) {
            alert('勾稽校验失败: ' + err.message);
        } finally {
            btnRunQuality.removeAttribute('disabled');
            btnRunQuality.textContent = '🧪 运行勾稽校验';
        }
    });
}


// ─────────────────────────────────────────────
// 12. Pipeline Runner Trigger
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
        const data = await fetchJson(`${API}/api/run-pipeline`, {
            method: 'POST'
        });

        pipelineAlert.className = 'alert-box success';
        pipelineAlert.innerHTML = `<span>✅</span> <div><strong>数仓清洗成功！</strong> DWD 维度表、DWS 财务指标面板与 ADS 行业聚合集市已重构！${data.message}</div>`;

        hideOnboarding();
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
// 12b. Refresh Data Runner（一键更新数据：抓取 → 导入 → 清洗）
// ─────────────────────────────────────────────
const btnRefreshData = $('#btn-refresh-data');
const refreshAlert = $('#refresh-status-alert');
let refreshPollTimer = null;

function renderRefreshStatus(st) {
    refreshAlert.style.display = 'flex';
    if (st.running) {
        refreshAlert.className = 'alert-box loading';
        const stepText = st.step_label ? `当前步骤：${st.step_label}` : '正在初始化任务...';
        refreshAlert.innerHTML = `<span>⏳</span> <div><strong>数据更新进行中</strong>（${stepText}），全量抓取约需 10-60 分钟，请耐心等待，可随时离开本页面。</div>`;
    } else if (st.success === true) {
        refreshAlert.className = 'alert-box success';
        refreshAlert.innerHTML = `<span>✅</span> <div><strong>数据更新成功！</strong>${st.message || ''}（完成于 ${st.finished_at || '—'}）</div>`;
    } else if (st.success === false) {
        refreshAlert.className = 'alert-box error';
        refreshAlert.innerHTML = `<span>❌</span> <div><strong>数据更新失败！</strong>${st.message || ''}</div>`;
    } else {
        refreshAlert.style.display = 'none';
    }
}

async function pollRefreshStatus() {
    try {
        const st = await (await fetch(`${API}/api/refresh-data/status`)).json();
        renderRefreshStatus(st);
        if (!st.running) {
            clearInterval(refreshPollTimer);
            refreshPollTimer = null;
            btnRefreshData.removeAttribute('disabled');
            btnRefreshData.innerHTML = `🔄 更新数据（全量重抓 + 重建）`;
            if (st.success === true) {
                // 数据已重建，刷新页面各面板
                hideOnboarding();
                await checkApi();
                await loadStats();
                await loadDescriptive();
                await loadIndustryChart();
                await loadDistChart('roa');
                await loadCompanies();
                await loadInventory();
            }
        }
    } catch (_) {}
}

if (btnRefreshData) {
    btnRefreshData.addEventListener('click', async () => {
        // 防误触：该操作会全量重抓并重建数仓，必须先经用户确认
        const confirmed = confirm(
            '⚠️ 确认更新数据？\n\n' +
            '此操作将重新抓取 akshare 全市场行情并重算全部数据，耗时约 10-60 分钟，' +
            '期间数据库会被占用，看板查询可能变慢或暂时失败。\n\n' +
            '确定要继续吗？'
        );
        if (!confirmed) return;

        btnRefreshData.setAttribute('disabled', 'true');
        btnRefreshData.innerHTML = `⏳ 正在启动数据更新...`;
        try {
            const data = await fetchJson(`${API}/api/refresh-data`, { method: 'POST' });
            if (!refreshPollTimer) {
                refreshPollTimer = setInterval(pollRefreshStatus, 3000);
            }
            await pollRefreshStatus();
        } catch (err) {
            refreshAlert.style.display = 'flex';
            refreshAlert.className = 'alert-box error';
            refreshAlert.innerHTML = `<span>❌</span> <div><strong>无法启动数据更新：</strong>${err.message}</div>`;
            btnRefreshData.removeAttribute('disabled');
            btnRefreshData.innerHTML = `🔄 更新数据（全量重抓 + 重建）`;
        }
    });

    // 页面加载时若已有任务在后台运行，恢复轮询与禁用状态
    (async () => {
        try {
            const st = await (await fetch(`${API}/api/refresh-data/status`)).json();
            if (st.running) {
                btnRefreshData.setAttribute('disabled', 'true');
                btnRefreshData.innerHTML = `⏳ 数据更新中...`;
                renderRefreshStatus(st);
                refreshPollTimer = setInterval(pollRefreshStatus, 3000);
            }
        } catch (_) {}
    })();
}


// ─────────────────────────────────────────────
// 13. Smart Entity Resolution & Alignment
// ─────────────────────────────────────────────
const thresholdSlider = $('#align-threshold');
const thresholdVal = $('#align-threshold-val');

if (thresholdSlider && thresholdVal) {
    thresholdSlider.addEventListener('input', (e) => {
        thresholdVal.textContent = Number(e.target.value).toFixed(2);
    });
}

const btnRunAlign = $('#btn-run-align');
let alignedResultsCache = null;

if (btnRunAlign) {
    btnRunAlign.addEventListener('click', async () => {
        const namesInput = $('#align-names-input').value;
        const threshold = parseFloat(thresholdSlider.value);

        const names = namesInput.split(/[\n,]/).map(n => n.trim()).filter(n => n !== '');

        if (names.length === 0) {
            alert('请在文本框中输入至少一个企业名称。');
            return;
        }

        btnRunAlign.setAttribute('disabled', 'true');
        btnRunAlign.innerHTML = `⏳ 正在实体对齐中...`;

        const emptyState = $('#align-empty-state');
        const summaryRow = $('#align-summary-row');
        const resultsBlock = $('#align-results-block');

        try {
            const data = await fetchJson(`${API}/api/resolve`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ names, threshold })
            });

            alignedResultsCache = data;

            const total = data.length;
            const matchedCount = data.filter(d => d.match_type !== 'No Match').length;
            const exactCount = data.filter(d => d.match_type === 'Current Exact Match' || d.match_type === 'Historical Exact Match').length;
            const successRate = total > 0 ? (matchedCount / total * 100).toFixed(1) + '%' : '0%';

            emptyState.style.display = 'none';
            summaryRow.style.display = 'grid';
            resultsBlock.style.display = 'block';

            $('#align-stat-total').textContent = total;
            $('#align-stat-success').textContent = successRate;
            $('#align-stat-exact').textContent = exactCount;

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

        let csvContent = '﻿';
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
