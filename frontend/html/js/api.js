/**
 * API 层 - 封装所有后端接口调用
 * 后端地址由 nginx 反向代理到 localhost:8085，故使用相对路径
 */

const Api = (() => {
    const BASE_URL = '';
    let _token = null;

    function setToken(token) {
        _token = token;
    }

    function getToken() {
        return _token;
    }

    function clearToken() {
        _token = null;
    }

    async function request(url, options = {}) {
        const config = {
            headers: { 'Accept': 'application/json' },
            ...options,
        };

        // 非 FormData 请求自动添加 JSON Content-Type
        if (!(config.body instanceof FormData)) {
            config.headers['Content-Type'] = 'application/json';
        }

        // 携带 token
        if (_token) {
            config.headers['Authorization'] = 'Bearer ' + _token;
        }

        try {
            const response = await fetch(`${BASE_URL}${url}`, config);
            const result = await response.json();

            if (result.code === 200) {
                return result.data;
            }
            throw new Error(result.message || '请求失败');
        } catch (err) {
            if (err.message === 'Failed to fetch') {
                throw new Error('网络连接失败，请检查后端服务是否启动');
            }
            throw err;
        }
    }

    return {
        setToken,
        getToken,
        clearToken,
        // 用户模块
        sendCode(phone) {
            return request('/user/send-code', {
                method: 'POST',
                body: JSON.stringify({ phone }),
            });
        },

        login(phone, code) {
            return request('/user/login', {
                method: 'POST',
                body: JSON.stringify({ phone, code }),
            });
        },

        // 文档模块
        uploadDocument(file, userId, permission) {
            const formData = new FormData();
            formData.append('file', file);
            formData.append('userId', userId);
            formData.append('permission', permission);
            return request('/document/upload', {
                method: 'POST',
                body: formData,
            });
        },

        listDocuments(userId) {
            return request(`/document/list?userId=${userId}`);
        },

        getDocumentUrl(documentId) {
            return request(`/document/${documentId}/url`);
        },

        deleteDocument(documentId, userId) {
            return request(`/document/${documentId}?userId=${userId}`, {
                method: 'DELETE',
            });
        },

        reIngestDocument(documentId, userId) {
            return request(`/document/${documentId}/re-ingest?userId=${userId}`, {
                method: 'POST',
            });
        },

        checkDuplicate(fileName, userId) {
            return request(`/document/check-duplicate?fileName=${encodeURIComponent(fileName)}&userId=${userId}`);
        },

        overwriteDocument(documentId, file, userId, permission) {
            const formData = new FormData();
            formData.append('file', file);
            formData.append('userId', userId);
            formData.append('permission', permission);
            return request(`/document/${documentId}/overwrite`, {
                method: 'POST',
                body: formData,
            });
        },

        // 用户信息更新
        updateUser(data) {
            return request('/user/update', {
                method: 'PUT',
                body: JSON.stringify(data),
            });
        },

        // 删除用户
        deleteUser(userId) {
            return request(`/user/delete?userId=${userId}`, {
                method: 'DELETE',
            });
        },

        // 问答模块
        ask(question, sessionId, strategy) {
            return request('/qa/ask', {
                method: 'POST',
                body: JSON.stringify({ question, sessionId, strategy }),
            });
        },

        getQaHistory(sessionId) {
            return request(`/qa/history?sessionId=${sessionId}`);
        },

        deleteQaHistory(id) {
            return request(`/qa/history/${id}`, {
                method: 'DELETE',
            });
        },

        deleteBatchQaHistory(ids) {
            return request('/qa/history/batch', {
                method: 'DELETE',
                body: JSON.stringify(ids),
            });
        },

        // 会话模块
        createSession() {
            return request('/qa/session', {
                method: 'POST',
            });
        },

        getSessions() {
            return request('/qa/sessions');
        },

        deleteSession(id) {
            return request(`/qa/session/${id}`, {
                method: 'DELETE',
            });
        },
    };
})();
