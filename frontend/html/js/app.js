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

        // 问答
        qaInput: $('#qaInput'),
        qaSendBtn: $('#qaSendBtn'),
        qaStrategyGroup: $('#qaStrategyGroup'),
        qaMessages: $('#qaMessages'),
        newSessionBtn: $('#newSessionBtn'),
        sessionList: $('#sessionList'),
        deleteSessionBtn: $('#deleteSessionBtn'),
        currentSessionTitle: $('#currentSessionTitle'),
        sessionSubtitle: $('#sessionSubtitle'),

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
            tbody.innerHTML = `<tr id="emptyRow"><td colspan="7" class="empty-state">暂无文档，请上传</td></tr>`;
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
                    </td>
                    <td><span class="type-tag ${fileInfo.iconClass}">${fileInfo.label}</span></td>
                    <td class="file-size">${formatSize(doc.fileSize)}</td>
                    <td><span class="perm-tag ${doc.permission === 0 ? 'public' : 'private'}">${permissionLabel}</span></td>
                    <td><span class="status-tag ${statusInfo.class}">${statusInfo.text}</span></td>
                    <td class="file-size">${formatTime(doc.createTime)}</td>
                    <td>
                        <div class="action-btns">
                            <button class="btn btn-secondary btn-sm" data-action="url" data-id="${doc.id}">获取链接</button>
                            ${isOwner ? `<button class="btn btn-danger btn-sm" data-action="delete" data-id="${doc.id}">删除</button>` : ''}
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

        const lines = html.split('\n');
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
            Api.clearToken();
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
            container.innerHTML = '<div class="qa-session-empty">暂无对话</div>';
            return;
        }

        container.innerHTML = state.sessions.map(s => {
            const title = s.title || '新对话';
            const isActive = s.id === state.currentSessionId;
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
            els.qaInput.placeholder = '请先新建或选择一个对话';
        } else {
            els.qaInput.placeholder = '请输入您的问题...';
        }
    }

    function updateQaHeader() {
        if (state.currentSessionId) {
            const session = state.sessions.find(s => s.id === state.currentSessionId);
            const title = session?.title || '新对话';
            els.currentSessionTitle.textContent = title;
            els.sessionSubtitle.textContent = '在当前对话中提问';
            els.deleteSessionBtn.classList.remove('hidden');
            enableQaInput(true);
        } else {
            els.currentSessionTitle.textContent = '智能问答';
            els.sessionSubtitle.textContent = '请选择或新建一个对话';
            els.deleteSessionBtn.classList.add('hidden');
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
                const exists = state.sessions.some(s => s.id === state.currentSessionId);
                if (!exists) {
                    state.currentSessionId = null;
                }
            }

            // 没有选中时自动选中第一个
            if (!state.currentSessionId && state.sessions.length > 0) {
                state.currentSessionId = state.sessions[0].id;
            }

            renderSessionList();
            updateQaHeader();

            // 加载当前会话消息
            if (state.currentSessionId) {
                loadQaHistory();
            } else {
                renderQaMessages([]);
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
            renderQaMessages([]);
            els.qaInput.focus();
            showToast('已创建新对话', 'success');
        } catch (err) {
            showToast(err.message || '创建对话失败', 'error');
        }
    }

    async function handleSwitchSession(sessionId) {
        if (sessionId === state.currentSessionId) return;
        state.currentSessionId = sessionId;
        renderSessionList();
        updateQaHeader();
        loadQaHistory();
    }

    async function handleDeleteSession(e) {
        e.stopPropagation();
        const sessionId = parseInt(e.currentTarget.dataset.sessionId);
        const session = state.sessions.find(s => s.id === sessionId);
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
    // 问答
    // ============================================
    let qaLoading = false;

    function renderQaMessages(messages) {
        const container = els.qaMessages;
        if (!messages || messages.length === 0) {
            container.innerHTML = '<div class="qa-empty">暂无问答记录，请在下方提问</div>';
            return;
        }
        container.innerHTML = messages.map(msg => `
            <div class="qa-message-group" data-id="${msg.id}">
                <div class="qa-group-controls">
                    <button class="qa-delete-single" data-id="${msg.id}" title="删除此条记录">&times;</button>
                </div>
                <div class="qa-msg qa-question">
                    <div class="qa-msg-label">问</div>
                    <div class="qa-msg-content">${escapeHtml(msg.question)}</div>
                    <div class="qa-msg-time">${formatTime(msg.createTime)}</div>
                </div>
                <div class="qa-msg qa-answer">
                    <div class="qa-msg-label">答</div>
                    <div class="qa-msg-content markdown-body">${renderMarkdown(msg.answer)}</div>
                </div>
            </div>
        `).join('');
        container.scrollTop = container.scrollHeight;
    }

    async function loadQaHistory() {
        if (!state.currentSessionId) {
            renderQaMessages([]);
            return;
        }
        try {
            const list = await Api.getQaHistory(state.currentSessionId);
            renderQaMessages(list || []);
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
            showToast('请先新建一个对话', 'error');
            return;
        }
        if (qaLoading) return;

        els.qaInput.value = '';

        const emptyEl = els.qaMessages.querySelector('.qa-empty');
        if (emptyEl) emptyEl.remove();

        const tempId = 'temp-' + Date.now();
        els.qaMessages.insertAdjacentHTML('beforeend', `
            <div class="qa-message-group" id="${tempId}">
                <div class="qa-msg qa-question">
                    <div class="qa-msg-label">问</div>
                    <div class="qa-msg-content">${escapeHtml(question)}</div>
                </div>
                <div class="qa-msg qa-answer">
                    <div class="qa-msg-label">答</div>
                    <div class="qa-msg-content"><em>思考中...</em></div>
                </div>
            </div>
        `);
        els.qaMessages.scrollTop = els.qaMessages.scrollHeight;

        try {
            qaLoading = true;
            els.qaSendBtn.disabled = true;

            const strategyValues = [null, 'diversity', 'relevance'];
            const activeBtn = els.qaStrategyGroup.querySelector('.qa-strategy-btn.active');
            const strategy = strategyValues[parseInt(activeBtn.dataset.value)];
            await Api.ask(question, state.currentSessionId, strategy);
            // 重新加载历史列表 + 会话列表（标题可能已更新）
            await Promise.all([
                loadQaHistory(),
                loadSessions(),
            ]);
        } catch (err) {
            const temp = document.getElementById(tempId);
            if (temp) temp.remove();
            if (els.qaMessages.children.length === 0) {
                els.qaMessages.innerHTML = '<div class="qa-empty">暂无问答记录，请在下方提问</div>';
            }
            showToast(err.message || '提问失败', 'error');
        } finally {
            qaLoading = false;
            els.qaSendBtn.disabled = false;
        }
    }

    // ============================================
    // 问答记录单条删除
    // ============================================

    async function handleSingleDelete(id) {
        if (!confirm('确定要删除此问答记录吗？')) return;
        try {
            await Api.deleteQaHistory(id);
            showToast('删除成功', 'success');
            loadQaHistory();
        } catch (err) {
            showToast(err.message || '删除失败', 'error');
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
    async function uploadFile(file) {
        if (!state.userId) {
            showToast('请先登录', 'error');
            return;
        }

        let permission = 0;
        els.permissionRadios.forEach(r => {
            if (r.checked) permission = parseInt(r.value);
        });

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

    function handleFileSelect() {
        els.fileInput.click();
    }

    function handleFileInputChange() {
        const files = els.fileInput.files;
        if (files.length === 0) return;
        uploadFile(files[0]);
        els.fileInput.value = '';
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

        if (action === 'url') {
            try {
                target.disabled = true;
                target.textContent = '获取中...';
                const url = await Api.getDocumentUrl(id);
                try {
                    await navigator.clipboard.writeText(url);
                    showToast('链接已复制到剪贴板', 'success');
                } catch {
                    showToast(`链接: ${url}`, 'info');
                }
            } catch (err) {
                showToast(`获取链接失败: ${err.message}`, 'error');
            } finally {
                target.disabled = false;
                target.textContent = '获取链接';
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
                handleDeleteSession(e);
                return;
            }
            const item = e.target.closest('.qa-session-item');
            if (item) {
                handleSwitchSession(parseInt(item.dataset.sessionId));
            }
        });

        // 删除当前会话
        els.deleteSessionBtn.addEventListener('click', () => {
            if (!state.currentSessionId) return;
            const session = state.sessions.find(s => s.id === state.currentSessionId);
            const title = session?.title || '新对话';
            if (!confirm(`确定要删除对话"${title}"及其所有消息吗？`)) return;
            (async () => {
                try {
                    await Api.deleteSession(state.currentSessionId);
                    state.currentSessionId = null;
                    await loadSessions();
                    showToast('对话已删除', 'success');
                } catch (err) {
                    showToast(err.message || '删除失败', 'error');
                }
            })();
        });

        // 问答消息事件委托（单条删除）
        els.qaMessages.addEventListener('click', (e) => {
            const delBtn = e.target.closest('.qa-delete-single');
            if (delBtn) {
                handleSingleDelete(parseInt(delBtn.dataset.id));
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
            if (e.target === els.selectFileBtn || e.target.closest('.btn')) return;
            handleFileSelect();
        });
        els.fileInput.addEventListener('change', handleFileInputChange);
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
        enableQaInput(false);
        showToast('请先登录', 'info');
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
