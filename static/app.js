const serverSelect = document.getElementById('server');
const pathInput = document.getElementById('currentPath');
const logEl = document.getElementById('log');
const baseHttps = document.getElementById('baseHttps');
const progressFill = document.getElementById('progressFill');
const progressText = document.getElementById('progressText');
const progressPercent = document.getElementById('progressPercent');
const progressTitle = document.getElementById('progressTitle');

function setLog(msg, append = true) { logEl.textContent = append ? (logEl.textContent + '\n' + msg) : msg; }
function setProgress(percent, text, title = 'درحال اجرا') {
  const p = Math.max(0, Math.min(100, Math.round(percent)));
  progressFill.style.width = `${p}%`; progressPercent.textContent = `${p}%`; progressText.textContent = text; progressTitle.textContent = title;
}

(function init() {
  const servers = JSON.parse(document.getElementById('server-data').textContent);
  serverSelect.innerHTML = servers.map(s => `<option value="${s.id}">${s.name}</option>`).join('');
  serverSelect.onchange = () => { const selected = servers.find(s => s.id === serverSelect.value); baseHttps.value = selected?.base_https || ''; loadList(); };
  serverSelect.dispatchEvent(new Event('change'));
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

  setLog('⏳ شروع آپلود...', false); setProgress(5, `آماده‌سازی ${files.length} فایل برای آپلود...`, 'آپلود');

  const data = await new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/upload', true);
    xhr.upload.onprogress = (e) => {
      if (!e.lengthComputable) return;
      const percent = 10 + (e.loaded / e.total) * 80;
      setProgress(percent, `درحال ارسال دیتا: ${Math.round(e.loaded / 1024)}KB / ${Math.round(e.total / 1024)}KB`, 'آپلود فایل‌ها');
    };
    xhr.onreadystatechange = () => {
      if (xhr.readyState !== 4) return;
      try { resolve(JSON.parse(xhr.responseText)); } catch { reject(new Error('پاسخ سرور نامعتبر است')); }
    };
    xhr.onerror = () => reject(new Error('خطای شبکه در آپلود'));
    xhr.send(fd);
  }).catch(err => ({ ok: false, error: err.message }));

  if (!data.ok) { setLog('❌ ' + data.error); setProgress(0, 'آپلود با خطا متوقف شد', 'خطا'); return; }
  setProgress(95, 'آپلود تمام شد؛ درحال نهایی‌سازی لینک‌ها...', 'پایان آپلود');
  data.uploaded.forEach(item => { setLog(`✅ ${item.file} -> ${item.remote_path} (attempt ${item.attempt})`); if (item.download_link) setLog('🔗 ' + item.download_link); });
  setProgress(100, `تمام شد! ${data.uploaded.length} فایل آپلود شد.`, 'موفق');
  loadList();
}

async function uploadByUrl() {
  const file_url = document.getElementById('fileUrlInput').value.trim();
  if (!file_url) { setLog('لینک فایل وارد نشده'); return; }
  setProgress(10, 'درحال دانلود از لینک و آپلود به FTP...', 'آپلود با لینک');
  const res = await fetch('/api/upload-by-url', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      server_id: serverSelect.value,
      target_dir: pathInput.value,
      file_url,
      retries: document.getElementById('retries').value || '2',
      base_https: baseHttps.value
    })
  });
  const data = await res.json();
  if (!data.ok) { setLog('❌ ' + data.error); setProgress(0, 'ناموفق', 'خطا'); return; }
  data.uploaded.forEach(item => setLog(`✅ ${item.file} -> ${item.remote_path}`));
  setProgress(100, 'آپلود با لینک کامل شد', 'موفق');
  loadList();
}
