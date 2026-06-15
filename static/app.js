document.addEventListener('DOMContentLoaded', function () {
  const navButtons = document.querySelectorAll('.nav-item');
  const pages = document.querySelectorAll('.page');
  const navMap = {
    dashboard: 'home',
    downloads: 'downloads',
    history: 'history',
    logs: 'logs',
    settings: 'settings',
  };

  function showPage(pageKey) {
    pages.forEach(page => page.classList.toggle('active', page.id === navMap[pageKey]));
    navButtons.forEach(btn => btn.classList.toggle('active', btn.dataset.target === pageKey));
  }

  navButtons.forEach(button => {
    button.addEventListener('click', () => showPage(button.dataset.target));
  });

  async function refreshStatistics() {
    try {
      const response = await fetch('/api/statistics');
      const data = await response.json();
      document.getElementById('statTotal').textContent = data.total || 0;
      document.getElementById('statSuccess').textContent = data.success || 0;
      document.getElementById('statFailed').textContent = data.failed || 0;
      document.getElementById('statSize').textContent = `${data.total_size || 0} B`;
      document.getElementById('trendChart').textContent = (data.trend || [])
        .map(item => `${item.date}: ${item.count}`)
        .join('\n');
      document.getElementById('platformDistribution').textContent = Object.entries(data.by_platform || {})
        .map(([key, value]) => `${key}: ${value}`)
        .join('\n');
      document.getElementById('countVideo').textContent = data.media_counts?.video || 0;
      document.getElementById('countAudio').textContent = data.media_counts?.audio || 0;
      document.getElementById('countImage').textContent = data.media_counts?.image || 0;
    } catch (err) {
      console.error(err);
    }
  }

  async function refreshHistory() {
    const tbody = document.getElementById('historyTable');
    if (!tbody) return;
    const response = await fetch('/api/history');
    const data = await response.json();
    tbody.innerHTML = (data.history || []).map(item => `
      <tr>
        <td>${item.id}</td>
        <td>${item.url || ''}</td>
        <td>${item.platform || ''}</td>
        <td>${item.status || ''}</td>
        <td>${item.filesize || 0}</td>
        <td>${item.created_at ? new Date(item.created_at * 1000).toLocaleString() : ''}</td>
      </tr>
    `).join('');
  }

  async function refreshDownloads() {
    const tbody = document.getElementById('downloadsTable');
    if (!tbody) return;
    const response = await fetch('/api/downloads');
    const data = await response.json();
    tbody.innerHTML = (data.tasks || []).map(item => `
      <tr>
        <td>${item.id}</td>
        <td>${item.url || ''}</td>
        <td>${item.platform || ''}</td>
        <td>${item.status || ''}</td>
        <td>${item.progress ? Math.round(item.progress * 100) + '%' : '0%'}</td>
        <td>${item.speed || ''}</td>
        <td>${item.created_at ? new Date(item.created_at * 1000).toLocaleString() : ''}</td>
      </tr>
    `).join('');
  }

  async function refreshLogs() {
    const pre = document.getElementById('logContent');
    if (!pre) return;
    const response = await fetch('/api/logs');
    const data = await response.json();
    pre.textContent = (data.logs || []).join('\n');
  }

  async function refreshSettings() {
    const pre = document.getElementById('configContent');
    if (!pre) return;
    const response = await fetch('/api/settings');
    const data = await response.json();
    pre.textContent = JSON.stringify(data.config || {}, null, 2);
  }

  document.getElementById('refreshDownloads')?.addEventListener('click', refreshDownloads);
  showPage('dashboard');
  refreshStatistics();
  refreshHistory();
  refreshDownloads();
  refreshLogs();
  refreshSettings();

  const downloadFormMain = document.getElementById('downloadForm');
  const downloadFormCard = document.getElementById('downloadFormCard');

  async function submitDownload(data, submitButton, submitText) {
    const originalText = submitButton ? submitButton.innerText : '';
    if (submitButton) {
      submitButton.disabled = true;
      submitButton.innerText = '提交中...';
    }
    try {
      const response = await fetch('/api/submit_download', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
      const result = await response.json();
      const messageEl = document.getElementById('downloadMessage');
      if (response.ok) {
        if (messageEl) {
          messageEl.textContent = '已提交下载任务。请稍候查看日志。';
          messageEl.className = 'success';
        }
        if (document.getElementById('url')) {
          document.getElementById('url').value = '';
        }
        if (document.getElementById('title')) {
          document.getElementById('title').value = '';
        }
        if (document.getElementById('note')) {
          document.getElementById('note').value = '';
        }
        refreshDownloads();
        refreshHistory();
      } else {
        if (messageEl) {
          messageEl.textContent = result.message || '提交失败，请重试。';
          messageEl.className = 'error';
        }
      }
    } catch (err) {
      console.error(err);
    } finally {
      if (submitButton) {
        submitButton.disabled = false;
        submitButton.innerText = submitText || originalText;
      }
    }
  }

  if (downloadFormMain) {
    downloadFormMain.addEventListener('submit', async function (event) {
      event.preventDefault();
      const data = {
        platform: '',
        url: document.getElementById('url')?.value || '',
        author: '',
        title: '',
        note: '',
      };
      await submitDownload(data, document.getElementById('submitDownload'), '立即下载');
    });
  }

  if (downloadFormCard) {
    downloadFormCard.addEventListener('submit', async function (event) {
      event.preventDefault();
      const data = {
        url: document.getElementById('url')?.value || '',
        platform: document.getElementById('platform')?.value || '',
        author: document.getElementById('author')?.value || '',
        title: document.getElementById('title')?.value || '',
        note: document.getElementById('note')?.value || '',
      };
      await submitDownload(data, document.getElementById('submitDownloadCard'), '提交下载任务');
    });
  }
});