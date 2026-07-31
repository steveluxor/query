/**
 * RAG 知识管理系统 - 前端应用逻辑
 */
(() => {
    'use strict';

    // ============================================
    // 状态管理
    // ============================================
    const state = {
        user: null,
        userId: null,
        documents: [],
        countdownTimer: null,
        currentSessionId: null,
        sessions: [],
        reuploadDocId: null,
    };

    // ============================================
    // DOM 引用
    // ============================================
    const $ = (sel) => document.querySelector(sel);
    const $$ = (sel) => document.querySelectorAll(sel);

    const els = {
        // 页面容器
        loginPage: $('#loginPage'),
        mainPage: $('#mainPage'),

        // 登录表单
        phoneInput: $('#phoneInput'),
        codeInput: $('#codeInput'),
        sendCodeBtn: $('#sendCodeBtn'),
        loginBtn: $('#loginBtn'),

        // 头部
        userDisplay: $('#userDisplay'),
        logoutBtn: $('#logoutBtn'),

        // 上传
        uploadArea: $('#uploadArea'),
        fileInput: $('#fileInput'),
        selectFileBtn: $('#selectFileBtn'),
        uploadProgress: $('#uploadProgress'),
        reuploadFileInput: $('#reuploadFileInput'),
        progressFill: $('.progress-fill'),
        progressText: $('.progress-text'),
        permissionRadios: document.querySelectorAll('input[name="permission"]'),

        // 文档列表
        docTableBody: $('#docTableBody'),
        emptyRow: $('#emptyRow'),

        // 视图切换
        docTabBtn: $('#docTabBtn'),
        qaTabBtn: $('#qaTabBtn'),
        docSections: document.querySelectorAll('.upload-section, .document-list-section'),
        qaView: $('#qaView'),

        // 问答 - Agent Workspace
        qaInput: $('#qaInput'),
        qaSendBtn: $('#qaSendBtn'),
        qaStrategyGroup: $('#qaStrategyGroup'),
        agentTrace: $('#agentTrace'),
        resultWorkspace: $('#resultWorkspace'),
        resultTabs: $('#resultTabs'),
        resultContent: $('#resultContent'),
        dagContent: $('#dagContent'),
        newSessionBtn: $('#newSessionBtn'),
        sessionList: $('#sessionList'),

        // 个人信息
        profileBtn: $('#profileBtn'),
        profileModal: $('#profileModal'),
        closeProfileBtn: $('#closeProfileBtn'),
        profilePhone: $('#profilePhone'),
        profileUsername: $('#profileUsername'),
        profileEmail: $('#profileEmail'),
        profilePassword: $('#profilePassword'),
        saveProfileBtn: $('#saveProfileBtn'),
        deleteAccountBtn: $('#deleteAccountBtn'),

        // Toast
        toast: $('#toast'),
    };

    // ============================================
    // Toast 消息
    // ============================================
    function showToast(message, type = 'info') {
        const t = els.toast;
        t.textContent = message;
        t.className = `toast ${type}`;
        void t.offsetWidth;
        t.classList.remove('hidden');
        t.classList.add('show');
        clearTimeout(t._hideTimer);
        t._hideTimer = setTimeout(() => {
            t.classList.remove('show');
            t.classList.add('hidden');
        }, 3000);
    }

    // ============================================
    // 文件类型工具
    // ============================================
    function getFileTypeInfo(fileName, mimeType) {
        const ext = fileName.split('.').pop()?.toLowerCase() || '';
        if (['pdf'].includes(ext)) return { type: 'pdf', label: 'PDF', iconClass: 'pdf' };
        if (['doc', 'docx'].includes(ext) || mimeType?.includes('word')) return { type: 'word', label: 'Word', iconClass: 'word' };
        if (['txt'].includes(ext)) return { type: 'txt', label: 'TXT', iconClass: 'txt' };
        if (['png', 'jpg', 'jpeg', 'gif', 'svg', 'webp'].includes(ext)) return { type: 'image', label: '图片', iconClass: 'image' };
        return { type: 'other', label: ext.toUpperCase() || '文件', iconClass: 'other' };
    }

    function formatSize(bytes) {
        if (!bytes) return '-';
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
        return (bytes / (1024 * 1024)).toFixed(2) + ' MB';
    }

    function formatTime(isoStr) {
        if (!isoStr) return '-';
        try {
            const d = new Date(isoStr);
            const pad = (n) => String(n).padStart(2, '0');
            return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
        } catch {
            return isoStr;
        }
    }

    function getStatusInfo(status) {
        const map = {
            'UPLOADED': { text: '已上传', class: 'uploaded' },
            'PROCESSING': { text: '处理中', class: 'processing' },
            'COMPLETED': { text: '已完成', class: 'completed' },
            'FAILED': { text: '失败', class: 'failed' },
        };
        return map[status] || { text: status || '未知', class: 'other' };
    }

    // ============================================
    // 文档列表渲染
    // ============================================
    function renderDocumentList() {
        const tbody = els.docTableBody;
        if (state.documents.length === 0) {
            tbody.innerHTML = `<tr id="emptyRow"><td colspan="2" class="empty-state">暂无文档，请上传</td></tr>`;
            return;
        }

        tbody.innerHTML = state.documents.map((doc) => {
            const fileInfo = getFileTypeInfo(doc.fileName, doc.fileType);
            const statusInfo = getStatusInfo(doc.status);
            const isOwner = Number(doc.userId) === Number(state.userId);
            const permissionLabel = doc.permission === 0 ? '公开' : '私有';
            return `
                <tr>
                    <td>
                        <div class="file-name">
                            <span class="file-icon ${fileInfo.iconClass}">${fileInfo.label}</span>
                            <span class="file-name-text" title="${escapeHtml(doc.fileName)}">${escapeHtml(doc.fileName)}</span>
                        </div>
                        <div class="file-meta">
                            <span class="type-tag ${fileInfo.iconClass}">${fileInfo.label}</span>
                            <span class="file-size">${formatSize(doc.fileSize)}</span>
                            <span class="perm-tag ${doc.permission === 0 ? 'public' : 'private'}">${permissionLabel}</span>
                            <span class="status-tag ${statusInfo.class}">${statusInfo.text}</span>
                            <span class="file-time">${formatTime(doc.createTime)}</span>
                        </div>
                    </td>
                    <td>
                        <div class="action-btns">
                            ${doc.id ? `<button class="btn btn-secondary btn-sm" data-action="download" data-id="${doc.id}">下载</button>` : ''}
                            ${isOwner && doc.id ? `<button class="btn btn-secondary btn-sm" data-action="reupload" data-id="${doc.id}">重新上传</button>` : ''}
                            ${isOwner && doc.status === 'FAILED' && doc.id ? `<button class="btn btn-warning btn-sm" data-action="reingest" data-id="${doc.id}">重新向量化</button>` : ''}
                            ${isOwner && doc.id ? `<button class="btn btn-danger btn-sm" data-action="delete" data-id="${doc.id}">删除</button>` : ''}
                        </div>
                    </td>
                </tr>
            `;
        }).join('');
    }

    function escapeHtml(str) {
        if (!str) return '';
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    // ============================================
    // Markdown 渲染
    // ============================================
    function renderMarkdown(text) {
        if (!text) return '';

        let html = escapeHtml(text);

        // 代码块 (```)
        html = html.replace(/```(\w*)\n?([\s\S]*?)```/g, '<pre><code>$2</code></pre>');

        // 表格处理
        const lines = html.split('\n');
        let i = 0;
        while (i < lines.length) {
            // 检测表格：第一行有 |，第二行是分隔符 |---|
            if (i + 1 < lines.length &&
                lines[i].includes('|') &&
                /^\|[\s\-:|]+\|$/.test(lines[i + 1].trim())) {
                const tableLines = [];
                while (i < lines.length && lines[i].includes('|')) {
                    tableLines.push(lines[i]);
                    i++;
                }
                // 解析表格
                const headerCells = tableLines[0].split('|').map(c => c.trim()).filter(c => c);
                let tableHtml = '<table><thead><tr>';
                headerCells.forEach(c => { tableHtml += `<th>${c}</th>`; });
                tableHtml += '</tr></thead><tbody>';
                for (let j = 2; j < tableLines.length; j++) {
                    const cells = tableLines[j].split('|').map(c => c.trim()).filter(c => c);
                    if (cells.length > 0) {
                        tableHtml += '<tr>';
                        cells.forEach(c => { tableHtml += `<td>${c}</td>`; });
                        tableHtml += '</tr>';
                    }
                }
                tableHtml += '</tbody></table>';
                lines.splice(i - tableLines.length, tableLines.length, tableHtml);
                i = i - tableLines.length + 1;
            } else {
                i++;
            }
        }

        const result = [];
        let inList = false;
        let listType = null;

        function closeList() {
            if (inList) {
                result.push(listType === 'ul' ? '</ul>' : '</ol>');
                inList = false;
                listType = null;
            }
        }

        function processInline(str) {
            return str
                .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
                .replace(/`(.+?)`/g, '<code>$1</code>');
        }

        for (let i = 0; i < lines.length; i++) {
            const line = lines[i];

            const hMatch = line.match(/^(#{1,3})\s+(.+)$/);
            if (hMatch) {
                closeList();
                const level = hMatch[1].length;
                result.push(`<h${level}>${processInline(hMatch[2])}</h${level}>`);
                continue;
            }

            const ulMatch = line.match(/^[-*]\s+(.+)$/);
            if (ulMatch) {
                if (!inList || listType !== 'ul') {
                    closeList();
                    result.push('<ul>');
                    inList = true;
                    listType = 'ul';
                }
                result.push(`<li>${processInline(ulMatch[1])}</li>`);
                continue;
            }

            const olMatch = line.match(/^\d+\.\s+(.+)$/);
            if (olMatch) {
                if (!inList || listType !== 'ol') {
                    closeList();
                    result.push('<ol>');
                    inList = true;
                    listType = 'ol';
                }
                result.push(`<li>${processInline(olMatch[1])}</li>`);
                continue;
            }

            if (line.trim() === '') {
                closeList();
                continue;
            }

            closeList();
            result.push(`<p>${processInline(line)}</p>`);
        }

        closeList();
        return result.join('\n');
    }

    // ============================================
    // 登录逻辑
    // ============================================
    function startCountdown(seconds = 60) {
        if (state.countdownTimer) clearInterval(state.countdownTimer);
        const btn = els.sendCodeBtn;
        btn.disabled = true;
        let remaining = seconds;
        btn.textContent = `${remaining}s 后重发`;

        state.countdownTimer = setInterval(() => {
            remaining--;
            if (remaining <= 0) {
                clearInterval(state.countdownTimer);
                state.countdownTimer = null;
                btn.disabled = false;
                btn.textContent = '发送验证码';
            } else {
                btn.textContent = `${remaining}s 后重发`;
            }
        }, 1000);
    }

    async function handleSendCode() {
        const phone = els.phoneInput.value.trim();
        if (!/^1\d{10}$/.test(phone)) {
            showToast('请输入正确的11位手机号', 'error');
            return;
        }
        try {
            els.sendCodeBtn.disabled = true;
            els.sendCodeBtn.textContent = '发送中...';
            await Api.sendCode(phone);
            showToast('验证码已发送', 'success');
            startCountdown();
        } catch (err) {
            showToast(err.message || '发送失败', 'error');
            els.sendCodeBtn.disabled = false;
            els.sendCodeBtn.textContent = '发送验证码';
        }
    }

    async function handleLogin() {
        const phone = els.phoneInput.value.trim();
        const code = els.codeInput.value.trim();

        if (!/^1\d{10}$/.test(phone)) {
            showToast('请输入正确的手机号', 'error');
            return;
        }
        if (!code) {
            showToast('请输入验证码', 'error');
            return;
        }

        try {
            els.loginBtn.disabled = true;
            els.loginBtn.textContent = '登录中...';
            const data = await Api.login(phone, code);
            state.user = data?.user;
            state.userId = data?.user?.id;
            Api.setToken(data?.token);
            saveSession();
            showToast('登录成功', 'success');
            enterMainPage();
            loadDocuments();
        } catch (err) {
            showToast(err.message || '登录失败', 'error');
        } finally {
            els.loginBtn.disabled = false;
            els.loginBtn.textContent = '登 录';
        }
    }

    function saveSession() {
        if (state.userId) localStorage.setItem('rag_userId', state.userId);
        if (state.user) localStorage.setItem('rag_user', JSON.stringify(state.user));
    }

    function restoreSession() {
        const token = Api.getToken();
        const userId = localStorage.getItem('rag_userId');
        const userRaw = localStorage.getItem('rag_user');
        if (token && userId) {
            state.userId = parseInt(userId);
            if (userRaw) {
                try { state.user = JSON.parse(userRaw); } catch { /* ignore */ }
            }
            return true;
        }
        return false;
    }

    function clearSession() {
        localStorage.removeItem('rag_userId');
        localStorage.removeItem('rag_user');
        Api.clearToken();
    }

    // ============================================
    // 页面切换
    // ============================================
    function enterMainPage() {
        els.loginPage.classList.remove('active');
        els.mainPage.classList.add('active');
        els.userDisplay.textContent = state.user?.phone
            ? `用户: ${state.user.phone}`
            : `用户ID: ${state.userId}`;
    }

    function handleLogout() {
        Api.clearToken();
        state.user = null;
        state.userId = null;
        state.documents = [];
        state.currentSessionId = null;
        state.sessions = [];
        clearSession();
        els.phoneInput.value = '';
        els.codeInput.value = '';
        if (state.countdownTimer) {
            clearInterval(state.countdownTimer);
            state.countdownTimer = null;
            els.sendCodeBtn.disabled = false;
            els.sendCodeBtn.textContent = '发送验证码';
        }
        els.mainPage.classList.remove('active');
        els.loginPage.classList.add('active');
        renderDocumentList();
        showToast('已退出登录', 'info');
    }

    // ============================================
    // 个人信息
    // ============================================
    function toggleProfile() {
        const isVisible = els.profileModal.style.display !== 'none';
        els.profileModal.style.display = isVisible ? 'none' : 'flex';
        if (!isVisible && state.user) {
            els.profilePhone.value = state.user.phone || '';
            els.profileUsername.value = state.user.username || '';
            els.profileEmail.value = state.user.email || '';
            els.profilePassword.value = '';
        }
    }

    async function handleSaveProfile() {
        const username = els.profileUsername.value.trim();
        const email = els.profileEmail.value.trim();
        const password = els.profilePassword.value.trim();

        if (!username) {
            showToast('请输入用户名', 'error');
            return;
        }

        const data = { phone: state.user.phone };
        if (username) data.username = username;
        if (email) data.email = email;
        if (password) data.password = password;

        try {
            els.saveProfileBtn.disabled = true;
            els.saveProfileBtn.textContent = '保存中...';
            await Api.updateUser(data);
            state.user.username = username;
            state.user.email = email;
            showToast('个人信息更新成功', 'success');
            els.profileModal.style.display = 'none';
        } catch (err) {
            showToast(err.message || '更新失败', 'error');
        } finally {
            els.saveProfileBtn.disabled = false;
            els.saveProfileBtn.textContent = '保存修改';
        }
    }

    async function handleDeleteAccount() {
        if (!confirm('确定要注销账户吗？此操作将永久删除您的所有数据（文档、对话历史等），且无法恢复！')) return;
        if (!confirm('再次确认：您真的要注销账户吗？')) return;

        try {
            els.deleteAccountBtn.disabled = true;
            els.deleteAccountBtn.textContent = '注销中...';
            await Api.deleteUser(state.userId);
            els.profileModal.style.display = 'none';
            showToast('账户已注销', 'success');
            // 清除状态并返回登录页
            clearSession();
            state.user = null;
            state.userId = null;
            state.documents = [];
            state.currentSessionId = null;
            state.sessions = [];
            els.phoneInput.value = '';
            els.codeInput.value = '';
            if (state.countdownTimer) {
                clearInterval(state.countdownTimer);
                state.countdownTimer = null;
                els.sendCodeBtn.disabled = false;
                els.sendCodeBtn.textContent = '发送验证码';
            }
            els.mainPage.classList.remove('active');
            els.loginPage.classList.add('active');
            renderDocumentList();
        } catch (err) {
            showToast(err.message || '注销失败', 'error');
        } finally {
            els.deleteAccountBtn.disabled = false;
            els.deleteAccountBtn.textContent = '注销账户';
        }
    }

    // ============================================
    // 视图切换
    // ============================================
    function switchToDoc() {
        els.docSections.forEach(el => el.style.display = '');
        els.qaView.style.display = 'none';
        els.docTabBtn.classList.add('active-tab');
        els.qaTabBtn.classList.remove('active-tab');
    }

    function switchToQa() {
        els.docSections.forEach(el => el.style.display = 'none');
        els.qaView.style.display = '';
        els.qaTabBtn.classList.add('active-tab');
        els.docTabBtn.classList.remove('active-tab');
        loadSessions();
    }

    // ============================================
    // 会话管理
    // ============================================

    function renderSessionList() {
        const container = els.sessionList;
        if (!state.sessions || state.sessions.length === 0) {
            container.innerHTML = '<div class="session-empty">暂无任务</div>';
            return;
        }

        container.innerHTML = state.sessions.map(s => {
            const title = s.title || '新任务';
            const isActive = Number(s.id) === state.currentSessionId;
            return `
                <div class="qa-session-item ${isActive ? 'active' : ''}" data-session-id="${s.id}">
                    <span class="session-title" title="${escapeHtml(title)}">${escapeHtml(title)}</span>
                    <button class="session-delete" data-session-id="${s.id}" title="删除此对话">&times;</button>
                </div>
            `;
        }).join('');
    }

    function enableQaInput(enabled) {
        els.qaInput.disabled = !enabled;
        els.qaSendBtn.disabled = !enabled;
        if (!enabled) {
            els.qaInput.placeholder = '请先新建或选择一个任务';
        } else {
            els.qaInput.placeholder = '输入分析任务，例如：对比三个实验的差异...';
        }
    }

    function updateQaHeader() {
        if (state.currentSessionId) {
            enableQaInput(true);
        } else {
            enableQaInput(false);
        }
    }

    async function loadSessions() {
        if (!state.userId) return;
        try {
            const list = await Api.getSessions();
            state.sessions = list || [];

            // 如果当前 session 不在列表中（可能被删了），清除选中
            if (state.currentSessionId) {
                const exists = state.sessions.some(s => Number(s.id) === state.currentSessionId);
                if (!exists) {
                    state.currentSessionId = null;
                }
            }

            // 没有选中时自动选中第一个
            if (!state.currentSessionId && state.sessions.length > 0) {
                state.currentSessionId = Number(state.sessions[0].id);
            }

            renderSessionList();
            updateQaHeader();

            // 加载当前会话消息
            if (state.currentSessionId) {
                loadQaHistory();
            } else {
                renderHistoricalMessages([]);
            }
        } catch (err) {
            console.error('获取会话列表失败:', err);
        }
    }

    async function handleNewSession() {
        if (!state.userId) return;
        try {
            const session = await Api.createSession();
            state.currentSessionId = session.id;
            // 重新加载会话列表
            await loadSessions();
            // 清空消息区
            renderHistoricalMessages([]);
            els.qaInput.focus();
            showToast('已创建新对话', 'success');
        } catch (err) {
            showToast(err.message || '创建对话失败', 'error');
        }
    }

    async function handleSwitchSession(sessionId) {
        sessionId = Number(sessionId);
        if (!sessionId || sessionId === state.currentSessionId) return;
        state.currentSessionId = sessionId;
        renderSessionList();
        updateQaHeader();
        loadQaHistory();
    }

    async function handleDeleteSession(btn) {
        const rawId = btn.dataset.sessionId;
        const sessionId = Number(rawId);
        if (!sessionId || isNaN(sessionId)) return;
        const session = state.sessions.find(s => Number(s.id) === sessionId);
        const title = session?.title || '新对话';
        if (!confirm(`确定要删除对话"${title}"及其所有消息吗？`)) return;

        try {
            await Api.deleteSession(sessionId);
            // 如果删除的是当前会话，清除选中
            if (state.currentSessionId === sessionId) {
                state.currentSessionId = null;
            }
            await loadSessions();
            showToast('对话已删除', 'success');
        } catch (err) {
            showToast(err.message || '删除失败', 'error');
        }
    }

    // ============================================
    // Agent Workspace - 渲染
    // ============================================
    let qaLoading = false;
    let lastResponse = null; // 缓存最近一次响应

    const AGENT_LABELS = {
        retrieval: '知识检索',
        extraction: '知识提取',
        analysis: '数据分析',
        code: '代码执行',
        generator: '答案生成',
        critic: '质量审核',
        chat: '对话响应',
    };

    const AGENT_ICONS = {
        retrieval: '🔍',    // 🔍
        extraction: '🔎',   // 🔎
        analysis: '📊',     // 📊
        code: '💻',         // 💻
        generator: '✍️',    // ✍
        critic: '🧠',       // 🧠
        chat: '💬',         // 💬
    };

    function getAgentLabel(agent) {
        return AGENT_LABELS[agent] || agent;
    }

    function getAgentIcon(agent) {
        return AGENT_ICONS[agent] || '⚙️'; // ⚙️
    }

    // --- Agent Trace 时间线 ---
    function renderAgentTrace(plan, agentTrace) {
        if (!plan || plan.length === 0) return '';

        // 合并 plan + agentTrace，按 plan 顺序
        const steps = plan.map(task => {
            const trace = (agentTrace || []).find(t => t.name === task.agent);
            return {
                id: task.id,
                agent: task.agent,
                objective: task.objective || '',
                status: task.status || 'completed',
                durationMs: task.duration_ms || (trace && trace.duration_ms) || 0,
                summary: task.summary || (trace && trace.summary) || '',
                toolsUsed: task.tools_used || [],
                artifacts: task.artifacts || [],
                dependsOn: task.depends_on || [],
            };
        });

        let html = '<div class="trace-steps">';
        steps.forEach((step, i) => {
            const icon = getAgentIcon(step.agent);
            const label = getAgentLabel(step.agent);
            const statusClass = step.status === 'failed' ? 'failed' : 'completed';
            const duration = step.durationMs >= 1000
                ? (step.durationMs / 1000).toFixed(1) + 's'
                : step.durationMs + 'ms';

            let toolsHtml = '';
            if (step.toolsUsed.length > 0) {
                toolsHtml = '<div class="trace-tools">' +
                    step.toolsUsed.map(t => `<span class="trace-tool-tag">${escapeHtml(t)}</span>`).join('') +
                    '</div>';
            }

            let objectiveHtml = '';
            if (step.objective) {
                objectiveHtml = `<div class="trace-objective">${escapeHtml(step.objective)}</div>`;
            }

            let detailHtml = '';
            const detailParts = [];
            if (step.toolsUsed.length > 0) detailParts.push(`<div class="trace-detail-row"><span class="trace-detail-label">工具</span><span>${step.toolsUsed.join(', ')}</span></div>`);
            if (step.summary) detailParts.push(`<div class="trace-detail-row"><span class="trace-detail-label">输出</span><span>${escapeHtml(step.summary)}</span></div>`);
            if (step.artifacts.length > 0) detailParts.push(`<div class="trace-detail-row"><span class="trace-detail-label">产物</span><span>${step.artifacts.join(', ')}</span></div>`);
            if (step.dependsOn.length > 0) detailParts.push(`<div class="trace-detail-row"><span class="trace-detail-label">依赖</span><span>${step.dependsOn.join(', ')}</span></div>`);
            if (detailParts.length > 0) {
                detailHtml = `<div class="trace-detail" id="detail-${step.id}">${detailParts.join('')}</div>`;
            }

            if (i > 0) {
                html += '<div class="trace-arrow">↓</div>';
            }

            html += `
                <div class="trace-step ${statusClass}" data-step-id="${step.id}">
                    <div class="trace-icon">${icon}</div>
                    <div class="trace-body">
                        <div class="trace-header" onclick="toggleTraceDetail('${step.id}')">
                            <span class="trace-agent-name">${label} (${step.agent})</span>
                            <span class="trace-duration">${duration}</span>
                        </div>
                        ${objectiveHtml}
                        ${toolsHtml}
                        ${detailHtml}
                    </div>
                </div>`;
        });
        html += '</div>';

        return html;
    }

    // 全局函数：展开/折叠 Agent 详情
    window.toggleTraceDetail = function(stepId) {
        const detail = document.getElementById('detail-' + stepId);
        if (detail) {
            detail.classList.toggle('open');
        }
    };

    // --- 结果标签页 ---
    function renderResultTabs(response) {
        lastResponse = response;
        const workspace = els.resultWorkspace;
        const content = els.resultContent;

        if (!response) {
            workspace.style.display = 'none';
            return;
        }

        workspace.style.display = '';

        // 确定可用标签页
        const tabs = [];
        if (response.answer) tabs.push({ id: 'answer', label: '回答' });
        if (response._traceHtml) tabs.push({ id: 'trace', label: '执行过程' });
        if (response.image_urls && response.image_urls.length > 0) tabs.push({ id: 'charts', label: '图表' });
        if (hasCodeResult(response)) tabs.push({ id: 'code', label: '代码' });
        if (response.sources && response.sources.length > 0) tabs.push({ id: 'sources', label: '来源' });

        if (tabs.length === 0) {
            workspace.style.display = 'none';
            return;
        }

        // 渲染标签栏
        const tabsHtml = tabs.map((t, i) =>
            `<button class="result-tab ${i === 0 ? 'active' : ''}" data-tab="${t.id}">${t.label}</button>`
        ).join('');
        els.resultTabs.innerHTML = tabsHtml;

        // 绑定标签切换
        els.resultTabs.querySelectorAll('.result-tab').forEach(btn => {
            btn.addEventListener('click', () => {
                els.resultTabs.querySelectorAll('.result-tab').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                renderTabContent(btn.dataset.tab, response);
            });
        });

        // 渲染第一个标签
        renderTabContent(tabs[0].id, response);
    }

    function hasCodeResult(response) {
        // 检查 plan 中是否有 code agent
        if (response.plan) {
            return response.plan.some(t => t.agent === 'code');
        }
        return false;
    }

    function renderTabContent(tabId, response) {
        const content = els.resultContent;

        switch (tabId) {
            case 'answer':
                content.innerHTML = `<div class="result-answer">${renderMarkdown(response.answer)}</div>`;
                break;

            case 'trace':
                content.innerHTML = `<div class="result-trace">${response._traceHtml}</div>`;
                break;

            case 'charts':
                const chartsHtml = response.image_urls.map((url, i) => {
                    const src = url.startsWith('data:') ? url : (url.startsWith('charts/') ? `/${url}` : `/charts/${url}`);
                    const isDataUrl = url.startsWith('data:');
                    return `
                        <div class="result-chart-card">
                            <img src="${src}" alt="图表 ${i + 1}" />
                            ${!isDataUrl ? `<div style="padding:8px 12px;text-align:right;"><a href="${src}" download style="font-size:12px;color:var(--primary);text-decoration:none;">下载</a></div>` : ''}
                        </div>`;
                }).join('');
                content.innerHTML = `<div class="result-charts">${chartsHtml}</div>`;
                break;

            case 'code':
                let codeHtml = '';

                // 生成的 Python 代码
                if (response.generated_code) {
                    codeHtml += '<div style="margin-bottom:16px;">';
                    codeHtml += '<div style="font-size:13px;font-weight:500;color:var(--gray-700);margin-bottom:8px;">📝 生成的 Python 代码</div>';
                    codeHtml += `<div class="result-code"><code>${escapeHtml(response.generated_code)}</code></div>`;
                    codeHtml += '</div>';
                }

                // 执行结果
                if (response.code_stdout) {
                    codeHtml += '<div style="margin-bottom:16px;">';
                    codeHtml += '<div style="font-size:13px;font-weight:500;color:var(--gray-700);margin-bottom:8px;">📤 执行输出</div>';
                    codeHtml += `<div class="result-code" style="background:var(--gray-900);"><code>${escapeHtml(response.code_stdout)}</code></div>`;
                    codeHtml += '</div>';
                }

                // 错误信息
                if (response.code_error) {
                    codeHtml += '<div style="margin-bottom:16px;">';
                    codeHtml += '<div style="font-size:13px;font-weight:500;color:#dc2626;margin-bottom:8px;">❌ 执行错误</div>';
                    codeHtml += `<div class="result-code" style="background:#fef2f2;border:1px solid #fecaca;"><code style="color:#991b1b;">${escapeHtml(response.code_error)}</code></div>`;
                    codeHtml += '</div>';
                }

                // 生成的图表
                if (response.image_urls && response.image_urls.length > 0) {
                    codeHtml += '<div style="margin-bottom:16px;">';
                    codeHtml += '<div style="font-size:13px;font-weight:500;color:var(--gray-700);margin-bottom:8px;">📊 生成的图表</div>';
                    codeHtml += '<div class="result-charts">' +
                        response.image_urls.map(url => {
                            const src = url.startsWith('data:') ? url : (url.startsWith('charts/') ? `/${url}` : `/charts/${url}`);
                            return `<div class="result-chart-card"><img src="${src}" alt="图表" /></div>`;
                        }).join('') + '</div>';
                    codeHtml += '</div>';
                }

                // 无数据时
                if (!codeHtml) {
                    const codeTask = response.plan ? response.plan.find(t => t.agent === 'code') : null;
                    if (codeTask && codeTask.summary) {
                        codeHtml = `<div style="color:var(--gray-500);font-size:13px;">${escapeHtml(codeTask.summary)}</div>`;
                    } else {
                        codeHtml = '<div style="color:var(--gray-400);">无代码执行记录</div>';
                    }
                }

                content.innerHTML = codeHtml;
                break;

            case 'sources':
                const sourcesHtml = response.sources.map(s => `
                    <div class="result-source-item">
                        <div class="result-source-name">${escapeHtml(s.file_name)}${s.score ? ` (${(s.score * 100).toFixed(0)}%)` : ''}</div>
                        <div class="result-source-content">${escapeHtml((s.content || '').substring(0, 200))}</div>
                    </div>
                `).join('');
                content.innerHTML = `<div class="result-sources">${sourcesHtml}</div>`;
                break;
        }
    }

    // --- DAG 简图 ---
    function renderDagGraph(plan, container) {
        container = container || els.dagContent;
        if (!plan || plan.length === 0) {
            container.innerHTML = '<div class="dag-empty">执行任务后显示</div>';
            return;
        }

        // 状态图例
        let html = `
            <div class="dag-legend">
                <div class="dag-legend-item"><span class="dag-legend-dot completed"></span>完成</div>
                <div class="dag-legend-item"><span class="dag-legend-dot failed"></span>失败</div>
                <div class="dag-legend-item"><span class="dag-legend-dot running"></span>执行中</div>
            </div>`;

        // 拓扑排序分层
        const levels = topologicalLevels(plan);
        html += '<div class="dag-graph">';

        // Planner 根节点
        html += '<div class="dag-row"><div class="dag-node planner" data-step-id="planner">Planner</div></div>';

        levels.forEach((level, li) => {
            html += '<div class="dag-row">';
            level.forEach(task => {
                const icon = getAgentIcon(task.agent);
                const label = getAgentLabel(task.agent);
                const taskLabel = `${task.id} ${label}`;
                const duration = task.duration_ms >= 1000
                    ? (task.duration_ms / 1000).toFixed(1) + 's'
                    : (task.duration_ms || '') + (task.duration_ms ? 'ms' : '');
                const tooltip = [
                    task.objective,
                    task.summary,
                    duration ? `耗时: ${duration}` : '',
                    task.tools_used?.length ? `工具: ${task.tools_used.join(', ')}` : '',
                ].filter(Boolean).join('\n');
                html += `<div class="dag-node ${task.status}" data-step-id="${task.id}" onclick="highlightTraceStep('${task.id}')" title="${escapeHtml(tooltip)}"><span class="dag-node-icon">${icon}</span>${escapeHtml(taskLabel)}</div>`;
            });
            html += '</div>';
        });

        html += '</div>';
        container.innerHTML = html;

        // 渲染后绘制 SVG 箭头
        requestAnimationFrame(() => drawDagArrows(container, plan, levels));
    }

    function drawDagArrows(container, plan, levels) {
        const graph = container.querySelector('.dag-graph');
        if (!graph) return;

        // 移除旧 SVG
        const oldSvg = graph.querySelector('svg.dag-arrows');
        if (oldSvg) oldSvg.remove();

        const graphRect = graph.getBoundingClientRect();
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.classList.add('dag-arrows');
        svg.style.width = graph.scrollWidth + 'px';
        svg.style.height = graph.scrollHeight + 'px';

        // 箭头标记定义
        const defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
        const marker = document.createElementNS('http://www.w3.org/2000/svg', 'marker');
        marker.setAttribute('id', 'arrowhead');
        marker.setAttribute('markerWidth', '8');
        marker.setAttribute('markerHeight', '6');
        marker.setAttribute('refX', '8');
        marker.setAttribute('refY', '3');
        marker.setAttribute('orient', 'auto');
        const polygon = document.createElementNS('http://www.w3.org/2000/svg', 'polygon');
        polygon.setAttribute('points', '0 0, 8 3, 0 6');
        polygon.setAttribute('fill', '#94a3b8');
        marker.appendChild(polygon);
        defs.appendChild(marker);
        svg.appendChild(defs);

        // 收集所有节点位置
        const nodePositions = {};
        graph.querySelectorAll('.dag-node').forEach(node => {
            const id = node.dataset.stepId;
            const r = node.getBoundingClientRect();
            nodePositions[id] = {
                cx: r.left + r.width / 2 - graphRect.left + graph.scrollLeft,
                top: r.top - graphRect.top + graph.scrollTop,
                bottom: r.bottom - graphRect.top + graph.scrollTop,
            };
        });

        // Planner → 第一层所有节点
        if (nodePositions['planner'] && levels.length > 0) {
            levels[0].forEach(task => {
                if (nodePositions[task.id]) {
                    drawArrow(svg, nodePositions['planner'].cx, nodePositions['planner'].bottom,
                              nodePositions[task.id].cx, nodePositions[task.id].top);
                }
            });
        }

        // 各层之间按 depends_on 画箭头
        const taskMap = {};
        plan.forEach(t => { taskMap[t.id] = t; });

        for (let li = 1; li < levels.length; li++) {
            levels[li].forEach(task => {
                if (!task.depends_on || !nodePositions[task.id]) return;
                task.depends_on.forEach(depId => {
                    if (nodePositions[depId]) {
                        drawArrow(svg, nodePositions[depId].cx, nodePositions[depId].bottom,
                                  nodePositions[task.id].cx, nodePositions[task.id].top);
                    }
                });
            });
        }

        graph.appendChild(svg);
    }

    function drawArrow(svg, x1, y1, x2, y2) {
        const midY = (y1 + y2) / 2;
        const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        path.setAttribute('d', `M${x1},${y1} C${x1},${midY} ${x2},${midY} ${x2},${y2}`);
        path.setAttribute('fill', 'none');
        path.setAttribute('stroke', '#94a3b8');
        path.setAttribute('stroke-width', '1.5');
        path.setAttribute('marker-end', 'url(#arrowhead)');
        svg.appendChild(path);
    }

    function topologicalLevels(plan) {
        const taskMap = {};
        plan.forEach(t => { taskMap[t.id] = t; });

        const levels = [];
        const placed = new Set();

        // BFS 分层
        let current = plan.filter(t => !t.depends_on || t.depends_on.length === 0);
        while (current.length > 0) {
            levels.push(current);
            current.forEach(t => placed.add(t.id));
            current = plan.filter(t =>
                !placed.has(t.id) &&
                t.depends_on.every(d => placed.has(d))
            );
        }

        return levels;
    }

    // 全局函数：点击 DAG 节点高亮对应 Trace 步骤
    window.highlightTraceStep = function(stepId) {
        // 高亮 DAG 节点
        document.querySelectorAll('.dag-node').forEach(n => n.classList.remove('highlight'));
        const node = document.querySelector(`.dag-node[data-step-id="${stepId}"]`);
        if (node) node.classList.add('highlight');

        // 高亮 Trace 步骤
        document.querySelectorAll('.trace-step').forEach(s => s.style.background = '');
        const step = document.querySelector(`.trace-step[data-step-id="${stepId}"]`);
        if (step) {
            step.style.background = 'var(--primary-light)';
            step.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
    };

    // --- 历史对话渲染：显示全部消息 + 可切换 Agent Trace/DAG ---
    let _historyMessages = []; // 保存当前会话的所有消息，供 switchToRound 使用

    function _parseJSON(val) {
        if (!val) return null;
        try { return typeof val === 'string' ? JSON.parse(val) : val; } catch { return null; }
    }

    function renderHistoricalMessages(messages) {
        const container = els.agentTrace;
        const workspace = els.resultWorkspace;

        if (!messages || messages.length === 0) {
            container.innerHTML = `
                <div class="trace-empty">
                    <div class="trace-empty-icon">💬</div>
                    <div class="trace-empty-text">暂无问答记录，在下方输入问题开始分析</div>
                </div>`;
            workspace.style.display = 'none';
            els.dagContent.innerHTML = '<div class="dag-empty">执行任务后显示</div>';
            return;
        }

        _historyMessages = messages;

        // 渲染所有消息为可点击的对话列表
        let html = '<div class="chat-history">';
        messages.forEach((msg, index) => {
            const time = formatTime(msg.createTime);
            const isLast = index === messages.length - 1;
            html += `<div class="chat-msg user-msg">
                <div class="chat-bubble user-bubble">
                    <div class="chat-text">${escapeHtml(msg.question)}</div>
                    <div class="chat-time">${time}</div>
                </div>
            </div>`;
            html += `<div class="chat-msg ai-msg clickable ${isLast ? 'active' : ''}" data-round="${index}" onclick="switchToRound(${index})">
                <div class="chat-bubble ai-bubble">
                    <div class="chat-text">${escapeHtml(msg.answer || '（无回答）')}</div>
                </div>
            </div>`;
        });
        html += '</div>';
        container.innerHTML = html;

        // 默认显示最后一轮
        _renderRound(messages.length - 1);
    }

    // 渲染指定轮次的 Agent Trace + DAG + 结果
    function _renderRound(index) {
        const msg = _historyMessages[index];
        if (!msg) return;

        const planData = _parseJSON(msg.plan);
        const traceData = _parseJSON(msg.agentTrace);

        // 生成 trace HTML（不再直接渲染到 DOM）
        const traceHtml = (planData && planData.length > 0) ? renderAgentTrace(planData, traceData) : '';

        // 渲染 DAG 到右侧栏
        renderDagGraph(planData);

        const imageUrls = _parseJSON(msg.imageUrls) || [];
        const sources = _parseJSON(msg.sources) || [];
        renderResultTabs({
            answer: msg.answer,
            image_urls: imageUrls,
            sources: sources,
            plan: planData,
            _traceHtml: traceHtml,
            generated_code: msg.generatedCode || '',
            code_stdout: msg.codeStdout || '',
            code_error: msg.codeError || '',
            code_success: msg.codeSuccess !== false,
        });
    }

    // 全局函数：点击切换到指定轮次
    window.switchToRound = function(index) {
        // 高亮选中
        els.agentTrace.querySelectorAll('.chat-msg.ai-msg').forEach(el => el.classList.remove('active'));
        const target = els.agentTrace.querySelector(`.chat-msg.ai-msg[data-round="${index}"]`);
        if (target) target.classList.add('active');

        // 切换显示
        _renderRound(index);
    };

    // ============================================
    // 问答 - API 调用
    // ============================================

    async function loadQaHistory() {
        if (!state.currentSessionId) {
            renderHistoricalMessages([]);
            return;
        }
        try {
            const list = await Api.getQaHistory(state.currentSessionId);
            console.log('[loadQaHistory] sessionId=', state.currentSessionId, 'count=', list?.length, 'data=', list);
            renderHistoricalMessages(list || []);
        } catch (err) {
            console.error('获取问答历史失败:', err);
        }
    }

    async function handleSendQuestion() {
        const question = els.qaInput.value.trim();
        if (!question) {
            showToast('请输入问题', 'error');
            return;
        }
        if (!state.currentSessionId) {
            showToast('请先新建一个任务', 'error');
            return;
        }
        if (qaLoading) return;

        els.qaInput.value = '';

        // 保留已有对话历史，在底部追加用户问题 + loading
        const existingChat = els.agentTrace.querySelector('.chat-history');
        const userBubble = `<div class="chat-msg user-msg">
            <div class="chat-bubble user-bubble">
                <div class="chat-text">${escapeHtml(question)}</div>
                <div class="chat-time">刚刚</div>
            </div>
        </div>`;
        const loadingBubble = `<div class="chat-msg ai-msg" id="loadingBubble">
            <div class="chat-bubble ai-bubble loading-bubble">
                <div class="chat-dots"><span></span><span></span><span></span></div>
            </div>
        </div>`;
        if (existingChat) {
            existingChat.insertAdjacentHTML('beforeend', userBubble + loadingBubble);
            existingChat.scrollTop = existingChat.scrollHeight;
        } else {
            els.agentTrace.innerHTML = `<div class="chat-history">${userBubble}${loadingBubble}</div>`;
        }
        els.resultWorkspace.style.display = 'none';
        els.dagContent.innerHTML = '<div class="dag-empty">规划中...</div>';

        try {
            qaLoading = true;
            els.qaSendBtn.disabled = true;

            const strategyValues = [null, 'diversity', 'relevance'];
            const activeBtn = els.qaStrategyGroup.querySelector('.qa-strategy-btn.active');
            const strategy = strategyValues[parseInt(activeBtn.dataset.value)];
            const response = await Api.ask(question, state.currentSessionId, strategy);

            // 重新加载完整对话历史（包含新消息 + Agent Trace/DAG 恢复）
            await loadQaHistory();

            // 更新会话标题
            if (state.currentSessionId) {
                const title = question.length > 30 ? question.substring(0, 30) + '...' : question;
                const sessionItem = document.querySelector(`.session-item[data-id="${state.currentSessionId}"] .session-title`);
                if (sessionItem) sessionItem.textContent = title;
            }
        } catch (err) {
            // 移除 loading 气泡，显示错误
            const loadingEl = document.getElementById('loadingBubble');
            if (loadingEl) loadingEl.remove();
            showToast(err.message || '执行失败', 'error');
        } finally {
            qaLoading = false;
            els.qaSendBtn.disabled = false;
        }
    }

    // ============================================
    // 文档列表加载
    // ============================================
    async function loadDocuments() {
        if (!state.userId) return;
        try {
            const list = await Api.listDocuments(state.userId);
            state.documents = list || [];
            renderDocumentList();
        } catch (err) {
            console.error('获取文档列表失败:', err);
        }
    }

    // ============================================
    // 文档上传
    // ============================================
    function showDuplicateDialog(fileName) {
        return new Promise((resolve) => {
            const modal = document.createElement('div');
            modal.className = 'duplicate-modal';
            modal.innerHTML = `
                <div class="duplicate-dialog">
                    <div class="duplicate-header">文件名已存在</div>
                    <div class="duplicate-body">
                        <p>文件 "<strong>${escapeHtml(fileName)}</strong>" 已存在，您要如何处理？</p>
                    </div>
                    <div class="duplicate-actions">
                        <button class="btn btn-primary" data-action="overwrite">覆盖旧版本</button>
                        <button class="btn btn-secondary" data-action="rename">改名上传</button>
                        <button class="btn btn-cancel" data-action="cancel">取消</button>
                    </div>
                </div>
            `;

            modal.addEventListener('click', (e) => {
                const action = e.target.dataset.action;
                if (action) {
                    modal.remove();
                    resolve(action);
                }
            });

            document.body.appendChild(modal);
        });
    }

    function promptNewName(originalFile) {
        return new Promise((resolve) => {
            const modal = document.createElement('div');
            modal.className = 'duplicate-modal';
            const nameParts = originalFile.name.split('.');
            const ext = nameParts.length > 1 ? '.' + nameParts.pop() : '';
            const baseName = nameParts.join('.');

            modal.innerHTML = `
                <div class="duplicate-dialog">
                    <div class="duplicate-header">输入新文件名</div>
                    <div class="duplicate-body">
                        <input type="text" class="rename-input" value="${escapeHtml(baseName)}(新版本)${ext}" />
                    </div>
                    <div class="duplicate-actions">
                        <button class="btn btn-primary" data-action="confirm">确认</button>
                        <button class="btn btn-cancel" data-action="cancel">取消</button>
                    </div>
                </div>
            `;

            const input = modal.querySelector('.rename-input');
            input.select();

            modal.addEventListener('click', (e) => {
                const action = e.target.dataset.action;
                if (action === 'confirm') {
                    const newName = input.value.trim();
                    if (!newName) {
                        showToast('请输入文件名', 'error');
                        return;
                    }
                    modal.remove();
                    const newFile = new File([originalFile], newName, { type: originalFile.type });
                    resolve(newFile);
                } else if (action === 'cancel') {
                    modal.remove();
                    resolve(null);
                }
            });

            input.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') {
                    modal.querySelector('[data-action="confirm"]').click();
                }
            });

            document.body.appendChild(modal);
        });
    }

    function pollDocumentStatus(docId, maxAttempts = 30) {
        let attempts = 0;
        const timer = setInterval(async () => {
            attempts++;
            try {
                const list = await Api.listDocuments(state.userId);
                const updated = list.find(d => String(d.id) === String(docId));
                if (updated && updated.status !== 'UPLOADED') {
                    const idx = state.documents.findIndex(d => String(d.id) === String(docId));
                    if (idx !== -1) state.documents[idx] = updated;
                    renderDocumentList();
                    clearInterval(timer);
                }
            } catch (_) { /* ignore polling errors */ }
            if (attempts >= maxAttempts) clearInterval(timer);
        }, 3000);
    }

    async function uploadFile(file) {
        if (!state.userId) {
            showToast('请先登录', 'error');
            return;
        }

        let permission = 0;
        els.permissionRadios.forEach(r => {
            if (r.checked) permission = parseInt(r.value);
        });

        try {
            const checkResult = await Api.checkDuplicate(file.name, state.userId);

            if (checkResult.exists) {
                if (checkResult.isOwner) {
                    const action = await showDuplicateDialog(file.name);
                    if (action === 'overwrite') {
                        return await overwriteUpload(file, checkResult.existingId, permission);
                    } else if (action === 'rename') {
                        file = await promptNewName(file);
                        if (!file) return;
                    } else {
                        return;
                    }
                } else {
                    showToast('该文件名已被其他用户使用', 'info');
                    file = await promptNewName(file);
                    if (!file) return;
                }
            }
        } catch (err) {
            showToast(`检查文件名失败: ${err.message}`, 'error');
            return;
        }

        els.uploadProgress.classList.remove('hidden');
        els.progressFill.style.width = '30%';
        els.progressText.textContent = '上传中...';

        try {
            const doc = await Api.uploadDocument(file, state.userId, permission);
            els.progressFill.style.width = '100%';
            els.progressText.textContent = '上传完成';

            state.documents.unshift(doc);
            renderDocumentList();
            showToast(`"${file.name}" 上传成功`, 'success');

            // 轮询等待消费者处理完成（更新状态）
            if (doc.id) pollDocumentStatus(doc.id);
        } catch (err) {
            els.progressFill.style.width = '0%';
            showToast(`上传失败: ${err.message}`, 'error');
        } finally {
            setTimeout(() => {
                els.uploadProgress.classList.add('hidden');
                els.progressFill.style.width = '0%';
            }, 2000);
        }
    }

    async function overwriteUpload(file, existingId, permission) {
        els.uploadProgress.classList.remove('hidden');
        els.progressFill.style.width = '30%';
        els.progressText.textContent = '覆盖上传中...';

        try {
            const doc = await Api.overwriteDocument(existingId, file, state.userId, permission);
            els.progressFill.style.width = '100%';
            els.progressText.textContent = '上传完成';

            const index = state.documents.findIndex(d => String(d.id) === String(existingId));
            if (index !== -1) {
                state.documents[index] = doc;
            }
            renderDocumentList();
            showToast(`"${file.name}" 覆盖上传成功`, 'success');
        } catch (err) {
            els.progressFill.style.width = '0%';
            showToast(`覆盖上传失败: ${err.message}`, 'error');
        } finally {
            setTimeout(() => {
                els.uploadProgress.classList.add('hidden');
                els.progressFill.style.width = '0%';
            }, 2000);
        }
    }

    function handleFileSelect() {
        els.fileInput.click();
    }

    function handleFileInputChange() {
        const files = els.fileInput.files;
        if (files.length === 0) return;
        uploadFile(files[0]);
        els.fileInput.value = '';
    }

    async function handleReuploadInputChange() {
        const files = els.reuploadFileInput.files;
        if (files.length === 0 || !state.reuploadDocId) return;

        const file = files[0];
        const docId = state.reuploadDocId;
        state.reuploadDocId = null;
        els.reuploadFileInput.value = '';

        let permission = 0;
        els.permissionRadios.forEach(r => {
            if (r.checked) permission = parseInt(r.value);
        });

        els.uploadProgress.classList.remove('hidden');
        els.progressFill.style.width = '30%';
        els.progressText.textContent = '重新上传中...';

        try {
            const doc = await Api.overwriteDocument(docId, file, state.userId, permission);
            els.progressFill.style.width = '100%';
            els.progressText.textContent = '上传完成';

            const index = state.documents.findIndex(d => String(d.id) === String(docId));
            if (index !== -1) {
                state.documents[index] = doc;
            }
            renderDocumentList();
            showToast(`"${file.name}" 重新上传成功`, 'success');
            if (doc.id) pollDocumentStatus(doc.id);
        } catch (err) {
            els.progressFill.style.width = '0%';
            showToast(`重新上传失败: ${err.message}`, 'error');
        } finally {
            setTimeout(() => {
                els.uploadProgress.classList.add('hidden');
                els.progressFill.style.width = '0%';
            }, 2000);
        }
    }

    // ============================================
    // 拖拽上传
    // ============================================
    function setupDragAndDrop() {
        const area = els.uploadArea;

        area.addEventListener('dragover', (e) => {
            e.preventDefault();
            area.classList.add('dragover');
        });

        area.addEventListener('dragleave', () => {
            area.classList.remove('dragover');
        });

        area.addEventListener('drop', (e) => {
            e.preventDefault();
            area.classList.remove('dragover');
            const files = e.dataTransfer.files;
            if (files.length > 0) {
                uploadFile(files[0]);
            }
        });
    }

    // ============================================
    // 文档操作（事件委托）
    // ============================================
    async function handleDocumentAction(target) {
        const action = target.dataset.action;
        const id = target.dataset.id;

        if (!id || id === 'undefined' || id === 'NaN') {
            showToast('文档ID无效', 'error');
            return;
        }

        if (action === 'download') {
            try {
                target.disabled = true;
                target.textContent = '下载中...';
                // 浏览器原生下载，token 放在 URL 中
                const link = document.createElement('a');
                link.href = `/document/${id}/download?token=${Api.getToken()}`;
                document.body.appendChild(link);
                link.click();
                document.body.removeChild(link);
                showToast('文件下载中...', 'success');
            } catch (err) {
                showToast(`下载失败: ${err.message}`, 'error');
            } finally {
                target.disabled = false;
                target.textContent = '下载';
            }
        } else if (action === 'delete') {
            try {
                target.disabled = true;
                target.textContent = '删除中...';
                await Api.deleteDocument(id, state.userId);
                state.documents = state.documents.filter(d => String(d.id) !== String(id));
                renderDocumentList();
                showToast('删除成功', 'success');
            } catch (err) {
                showToast(`删除失败: ${err.message}`, 'error');
                target.disabled = false;
                target.textContent = '删除';
            }
        } else if (action === 'reingest') {
            try {
                target.disabled = true;
                target.textContent = '处理中...';
                await Api.reIngestDocument(id, state.userId);
                const doc = state.documents.find(d => String(d.id) === String(id));
                if (doc) doc.status = 'COMPLETED';
                renderDocumentList();
                showToast('重新向量化成功', 'success');
            } catch (err) {
                showToast(`重新向量化失败: ${err.message}`, 'error');
                target.disabled = false;
                target.textContent = '重新向量化';
            }
        } else if (action === 'reupload') {
            state.reuploadDocId = id;
            els.reuploadFileInput.click();
        }
    }

    // ============================================
    // 事件绑定
    // ============================================
    function bindEvents() {
        // 登录
        els.sendCodeBtn.addEventListener('click', handleSendCode);
        els.loginBtn.addEventListener('click', handleLogin);

        els.codeInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') handleLogin();
        });
        els.phoneInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') els.codeInput.focus();
        });

        // 退出
        els.logoutBtn.addEventListener('click', handleLogout);

        // 视图切换
        els.docTabBtn.addEventListener('click', switchToDoc);
        els.qaTabBtn.addEventListener('click', switchToQa);

        // 问答
        els.qaSendBtn.addEventListener('click', handleSendQuestion);
        els.qaInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') handleSendQuestion();
        });
        els.newSessionBtn.addEventListener('click', handleNewSession);

        // 策略按钮组
        els.qaStrategyGroup.addEventListener('click', (e) => {
            const btn = e.target.closest('.qa-strategy-btn');
            if (!btn) return;
            els.qaStrategyGroup.querySelectorAll('.qa-strategy-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
        });

        // 会话列表事件委托（切换、删除）
        els.sessionList.addEventListener('click', (e) => {
            const delBtn = e.target.closest('.session-delete');
            if (delBtn) {
                handleDeleteSession(delBtn);
                return;
            }
            const item = e.target.closest('.qa-session-item');
            if (item) {
                handleSwitchSession(item.dataset.sessionId);
            }
        });

        // 个人信息
        els.profileBtn.addEventListener('click', toggleProfile);
        els.closeProfileBtn.addEventListener('click', toggleProfile);
        els.saveProfileBtn.addEventListener('click', handleSaveProfile);
        els.deleteAccountBtn.addEventListener('click', handleDeleteAccount);

        // 上传
        els.selectFileBtn.addEventListener('click', handleFileSelect);
        els.uploadArea.addEventListener('click', (e) => {
            if (e.target === els.selectFileBtn || e.target === els.reuploadFileInput || e.target === els.fileInput || e.target.closest('.btn')) return;
            handleFileSelect();
        });
        els.fileInput.addEventListener('change', handleFileInputChange);
        els.reuploadFileInput.addEventListener('change', handleReuploadInputChange);
        setupDragAndDrop();

        // 文档操作（事件委托）
        els.docTableBody.addEventListener('click', (e) => {
            const btn = e.target.closest('[data-action]');
            if (btn) handleDocumentAction(btn);
        });
    }

    // ============================================
    // 初始化
    // ============================================
    function init() {
        bindEvents();
        renderDocumentList();
        if (restoreSession()) {
            enterMainPage();
            loadDocuments();
        } else {
            enableQaInput(false);
            showToast('请先登录', 'info');
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
