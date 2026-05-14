const serverSelect = document.getElementById('server');
const pathInput = document.getElementById('currentPath');
const logEl = document.getElementById('log');
const baseHttps = document.getElementById('baseHttps');

function setLog(msg, append=true){ logEl.textContent = append ? (logEl.textContent + '\n' + msg) : msg; }

(function init(){
  const servers = JSON.parse(document.getElementById('server-data').textContent);
  serverSelect.innerHTML = servers.map(s => `<option value="${s.id}" data-base="${s.base_https}">${s.name}</option>`).join('');
  serverSelect.onchange = () => {
    const selected = servers.find(s=>s.id===serverSelect.value);
    baseHttps.value = selected?.base_https || '';
    loadList();
  };
  serverSelect.dispatchEvent(new Event('change'));
})();

async function loadList(){
  const res = await fetch(`/api/list?server_id=${encodeURIComponent(serverSelect.value)}&path=${encodeURIComponent(pathInput.value || '/')}`);
  const data = await res.json();
  if(!data.ok){ setLog('❌ ' + data.error); return; }
  pathInput.value = data.path;
  document.getElementById('items').innerHTML = data.items.map(item=>`<tr>
    <td>${item.type==='dir'?'📁':'📄'}</td><td>${item.name}</td><td>${item.type==='dir'?'—':item.size}</td>
    <td>${item.type==='dir'?`<button onclick="openDir('${item.path}')">باز کردن</button>`:''}<button onclick="removeItem('${item.path}','${item.type}')">حذف</button></td>
  </tr>`).join('');
}
function openDir(path){ pathInput.value = path; loadList(); }
function goUp(){ const parts = (pathInput.value || '/').split('/').filter(Boolean); parts.pop(); pathInput.value='/' + parts.join('/'); if(pathInput.value==='') pathInput.value='/'; loadList(); }

async function createFolder(){
  const folder_name = document.getElementById('newFolderName').value;
  const res = await fetch('/api/mkdir',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({server_id:serverSelect.value,path:pathInput.value,folder_name})});
  const data = await res.json(); if(!data.ok){setLog('❌ '+data.error); return;}
  setLog('✅ پوشه ساخته شد'); loadList();
}
async function removeItem(target_path, type){
  if(!confirm('حذف شود؟')) return;
  const res = await fetch('/api/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({server_id:serverSelect.value,target_path,type})});
  const data = await res.json(); if(!data.ok){setLog('❌ '+data.error); return;}
  setLog('🗑️ حذف شد: '+target_path); loadList();
}

async function uploadFiles(){
  const files = document.getElementById('fileInput').files;
  if(!files.length){ setLog('فایلی انتخاب نشده'); return; }
  const fd = new FormData(); for(const f of files) fd.append('files',f);
  fd.append('server_id',serverSelect.value); fd.append('target_dir',pathInput.value); fd.append('retries',document.getElementById('retries').value || '2'); fd.append('base_https',baseHttps.value);
  setLog('⏳ شروع آپلود...', false);
  const res = await fetch('/api/upload',{method:'POST',body:fd}); const data = await res.json();
  if(!data.ok){ setLog('❌ '+data.error); return; }
  data.uploaded.forEach(item=>{ setLog(`✅ ${item.file} -> ${item.remote_path} (attempt ${item.attempt})`); if(item.download_link) setLog('🔗 '+item.download_link); });
  loadList();
}
