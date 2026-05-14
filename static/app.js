const serverSelect = document.getElementById('server');
const pathInput = document.getElementById('currentPath');
const logEl = document.getElementById('log');
const baseHttps = document.getElementById('baseHttps');
const progressFill = document.getElementById('progressFill');
const progressText = document.getElementById('progressText');
const progressPercent = document.getElementById('progressPercent');
const progressTitle = document.getElementById('progressTitle');
const cancelTaskBtn = document.getElementById('cancelTaskBtn');

let activeTaskId = null;

function setLog(msg, append = true) { logEl.textContent = append ? (logEl.textContent + '\n' + msg) : msg; }
function setProgress(percent, text, title = 'درحال اجرا') {
  const p = Math.max(0, Math.min(100, Math.round(percent)));
  progressFill.style.width = `${p}%`; progressPercent.textContent = `${p}%`; progressText.textContent = text; progressTitle.textContent = title;
}
function setTaskRunning(taskId) {
  activeTaskId = taskId || null;
  cancelTaskBtn.style.display = activeTaskId ? 'block' : 'none';
}
async function cancelActiveTask() {
  if (!activeTaskId) return;
  const res = await fetch(`/api/tasks/${activeTaskId}/cancel`, { method: 'POST' });
  const data = await res.json();
  if (!data.ok) { setLog('❌ ' + data.error); return; }
  setLog('🛑 درخواست لغو ارسال شد');
}
cancelTaskBtn.onclick = cancelActiveTask;

async function waitTask(taskId, doneTitle) {
  setTaskRunning(taskId);
  try {
    while (true) {
      const res = await fetch(`/api/tasks/${taskId}`);
      const t = await res.json();
      if (!t.ok) throw new Error(t.error || 'Task failed');
      setProgress(t.progress || 0, t.text || '...', t.title || doneTitle);
      if (t.done) return t;
      await new Promise(r => setTimeout(r, 700));
    }
  } finally {
    setTaskRunning(null);
  }
}

(function init() {
  const servers = JSON.parse(document.getElementById('server-data').textContent);
  serverSelect.innerHTML = servers.map(s => `<option value="${s.id}">${s.name}</option>`).join('');
  serverSelect.onchange = () => { const selected = servers.find(s => s.id === serverSelect.value); baseHttps.value = selected?.base_https || ''; loadList(); };
  serverSelect.dispatchEvent(new Event('change'));
  loadLocalFiles();
})();

async function loadList() {
  setProgress(10, 'اتصال برای دریافت لیست فایل‌ها...', 'نمایش محتوا');
  const res = await fetch(`/api/list?server_id=${encodeURIComponent(serverSelect.value)}&path=${encodeURIComponent(pathInput.value || '/')}`);
  const data = await res.json();
  if (!data.ok) { setLog('❌ ' + data.error); setProgress(0, 'خطا در دریافت لیست'); return; }
  pathInput.value = data.path;
  document.getElementById('items').innerHTML = data.items.map(item => `<tr><td>${item.type === 'dir' ? '📁' : '📄'}</td><td>${item.name}</td><td>${item.type === 'dir' ? '—' : item.size}</td><td class="actions">${item.type === 'dir' ? `<button onclick="openDir('${item.path}')">باز کردن</button>` : ''}<button onclick="copyLink('${item.path}')">کپی لینک</button><button onclick="renameItem('${item.path}','${item.name}')">ویرایش نام</button><button onclick="removeItem('${item.path}','${item.type}')">حذف</button></td></tr>`).join('');
  setProgress(100, `لیست با ${data.items.length} آیتم آماده شد.`, 'آماده');
}
function copyLink(itemPath) {
  let base = (baseHttps.value || '').trim();
  if (base && !/^https?:\/\//i.test(base)) base = `http://${base}`;
  const link = base.replace(/\/$/, '') + itemPath;
  navigator.clipboard.writeText(link).then(() => setLog('🔗 کپی شد: ' + link));
}

async function renameItem(fromPath, currentName) {
  const new_name = prompt('نام جدید را وارد کنید:', currentName);
  if (!new_name || new_name === currentName) return;
  const res = await fetch('/api/rename', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ server_id: serverSelect.value, from_path: fromPath, new_name }) });
  const data = await res.json();
  if (!data.ok) { setLog('❌ ' + data.error); return; }
  setLog('✏️ تغییر نام انجام شد: ' + fromPath);
  loadList();
}

function openDir(path) { pathInput.value = path; loadList(); }
function goUp() { const parts = (pathInput.value || '/').split('/').filter(Boolean); parts.pop(); pathInput.value = '/' + parts.join('/'); if (pathInput.value === '') pathInput.value = '/'; loadList(); }

async function createFolder() {
  setProgress(25, 'درحال ساخت پوشه...', 'ساخت پوشه');
  const folder_name = document.getElementById('newFolderName').value;
  const res = await fetch('/api/mkdir', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ server_id: serverSelect.value, path: pathInput.value, folder_name }) });
  const data = await res.json();
  if (!data.ok) { setLog('❌ ' + data.error); setProgress(0, 'ساخت پوشه ناموفق بود'); return; }
  setLog('✅ پوشه ساخته شد'); setProgress(100, 'پوشه با موفقیت ساخته شد', 'موفق'); loadList();
}

async function removeItem(target_path, type) {
  if (!confirm('حذف شود؟')) return;
  setProgress(30, `درحال حذف ${target_path} ...`, 'حذف آیتم');
  const res = await fetch('/api/delete', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ server_id: serverSelect.value, target_path, type }) });
  const data = await res.json();
  if (!data.ok) { setLog('❌ ' + data.error); setProgress(0, 'حذف ناموفق بود'); return; }
  setLog('🗑️ حذف شد: ' + target_path); setProgress(100, 'حذف با موفقیت انجام شد', 'موفق'); loadList();
}

async function uploadFiles() {
  const files = document.getElementById('fileInput').files;
  if (!files.length) { setLog('فایلی انتخاب نشده'); return; }
  const fd = new FormData(); for (const f of files) fd.append('files', f);
  fd.append('server_id', serverSelect.value); fd.append('target_dir', pathInput.value); fd.append('retries', document.getElementById('retries').value || '2'); fd.append('base_https', baseHttps.value);

  setLog('⏳ ذخیره محلی...', false); setProgress(5, `ذخیره ${files.length} فایل در فضای محلی...`, 'ذخیره محلی');

  const data = await new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/upload', true);
    xhr.upload.onprogress = (e) => {
      if (!e.lengthComputable) return;
      const percent = 10 + (e.loaded / e.total) * 80;
      setProgress(percent, `درحال ذخیره دیتا: ${Math.round(e.loaded / 1024)}KB / ${Math.round(e.total / 1024)}KB`, 'ذخیره فایل‌ها');
    };
    xhr.onreadystatechange = () => {
      if (xhr.readyState !== 4) return;
      try { resolve(JSON.parse(xhr.responseText)); } catch { reject(new Error('پاسخ سرور نامعتبر است')); }
    };
    xhr.onerror = () => reject(new Error('خطای شبکه در آپلود'));
    xhr.send(fd);
  }).catch(err => ({ ok: false, error: err.message }));

  if (!data.ok) { setLog('❌ ' + data.error); setProgress(0, 'ذخیره ناموفق', 'خطا'); return; }
  setProgress(100, `ذخیره شد: ${data.saved.length} فایل`, 'موفق');
  data.saved.forEach(item => setLog(`✅ ذخیره محلی: ${item.file}`));
  loadLocalFiles();
}

async function loadLocalFiles() {
  const res = await fetch('/api/local/list');
  const data = await res.json();
  if (!data.ok) return;
  document.getElementById('localItems').innerHTML = data.items.map(item =>
    `<tr><td>${decodeURIComponent(item.name)}</td><td>${item.size_human}</td><td class="actions"><button onclick="uploadLocalToHost('${item.name}')">آپلود تو هاست</button><button onclick="renameLocal('${item.name}')">ادیت نام</button><button onclick="deleteLocal('${item.name}')">حذف</button></td></tr>`
  ).join('');
}

async function deleteLocal(name) {
  const res = await fetch('/api/local/delete', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }) });
  const data = await res.json();
  if (!data.ok) { setLog('❌ ' + data.error); return; }
  loadLocalFiles();
}

async function renameLocal(old_name) {
  const new_name = prompt('نام جدید:', old_name);
  if (!new_name || new_name === old_name) return;
  const res = await fetch('/api/local/rename', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ old_name, new_name }) });
  const data = await res.json();
  if (!data.ok) { setLog('❌ ' + data.error); return; }
  loadLocalFiles();
}

async function uploadLocalToHost(name) {
  const res = await fetch('/api/local/upload-to-host', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, server_id: serverSelect.value, target_dir: pathInput.value, base_https: baseHttps.value }) });
  const data = await res.json();
  if (!data.ok) { setLog('❌ ' + data.error); return; }
  const task = await waitTask(data.task_id, 'آپلود به هاست');
  if (!task.ok) { setLog('❌ ' + task.error); return; }
  setLog(`✅ آپلود شد: ${task.remote_path}`);
  if (task.download_link) setLog('🔗 ' + task.download_link);
  loadLocalFiles();
  loadList();
}

async function uploadByUrl() {
  const file_url = document.getElementById('fileUrlInput').value.trim();
  if (!file_url) { setLog('لینک فایل وارد نشده'); return; }
  setProgress(10, 'درحال دانلود از لینک و آپلود به FTP...', 'آپلود با لینک');
  const res = await fetch('/api/upload-by-url', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ file_url }) });
  const data = await res.json();
  if (!data.ok) { setLog('❌ ' + data.error); setProgress(0, 'ناموفق', 'خطا'); return; }
  const task = await waitTask(data.task_id, 'دانلود با لینک');
  if (!task.ok) { setLog('❌ ' + task.error); return; }
  task.saved.forEach(item => setLog(`✅ ذخیره محلی از لینک: ${decodeURIComponent(item.file)}`));
  setProgress(100, 'فایل لینک در فضای محلی ذخیره شد', 'موفق');
  loadLocalFiles();
}
