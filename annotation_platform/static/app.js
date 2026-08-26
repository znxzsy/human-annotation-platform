const randomId=()=>globalThis.crypto?.randomUUID?crypto.randomUUID():Array.from(crypto.getRandomValues(new Uint8Array(16)),x=>x.toString(16).padStart(2,'0')).join('');
const $=s=>document.querySelector(s);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const state={reviewer:'',csrf:'',summary:{total:0,statuses:{}},item:null,draft:[],recheckDraft:[],recheckSaving:false,focus:0,dirty:false,saving:false,timer:null,editRevision:0};
const OFFLINE_KEY='annotation-review-offline-v1';
const CONFLICT_KEY='annotation-review-conflicts-v1';
const params=new URLSearchParams(location.search),caseKind=params.get('kind')||'',isRecheckPool=['goodcase','badcase','unknown'].includes(caseKind),batchMode=isRecheckPool||caseKind==='wrong',selectedBatch=Number(params.get('batch')||(batchMode?1:0));
const batchStart=selectedBatch?(selectedBatch-1)*1000+1:1,batchEnd=selectedBatch?selectedBatch*1000:2147483647;
function loginUrl(){const next=location.pathname.split('/').pop()+location.search;return `login.html?next=${encodeURIComponent(next)}`}
async function api(path,opt={}){const method=(opt.method||'GET').toUpperCase();opt.headers={...(opt.headers||{}),'Content-Type':'application/json'};if(method!=='GET'&&state.csrf)opt.headers['X-CSRF-Token']=state.csrf;const r=await fetch(path,opt),v=await r.json();if(r.status===401){location.replace(loginUrl());throw new Error('登录已过期')}if(!r.ok){const err=new Error(v.detail||v.error||r.status);err.status=r.status;throw err}return v}
async function requireSession(){const r=await fetch('api/session',{cache:'no-store'}),v=await r.json();if(!v.authenticated){location.replace(loginUrl());throw new Error('请先登录')}state.reviewer=v.reviewer;state.csrf=v.csrf;$('#reviewer-name').textContent=state.reviewer;adoptLegacyOfflineDrafts()}
function adoptLegacyOfflineDrafts(){const all=readOffline();let changed=false;for(const record of Object.values(all)){if(record&&(!record.reviewer||String(record.reviewer).startsWith('标注员-'))){record.reviewer=state.reviewer;changed=true}}if(changed)localStorage.setItem(OFFLINE_KEY,JSON.stringify(all))}
const key=()=>randomId();
function readOffline(){try{return JSON.parse(localStorage.getItem(OFFLINE_KEY)||'{}')}catch{return {}}}
function offlineRecord(eventId=state.item?.event_id){return eventId?readOffline()[eventId]||null:null}
function writeOffline(record){const all=readOffline();all[record.eventId]=record;localStorage.setItem(OFFLINE_KEY,JSON.stringify(all))}
function removeOffline(eventId){const all=readOffline();delete all[eventId];localStorage.setItem(OFFLINE_KEY,JSON.stringify(all))}
function archiveConflict(record){if(!record)return;let all=[];try{all=JSON.parse(localStorage.getItem(CONFLICT_KEY)||'[]')}catch{}all.unshift({...record,conflictedAt:new Date().toISOString()});localStorage.setItem(CONFLICT_KEY,JSON.stringify(all.slice(0,20)))}
function submittedReadonly(){return state.item?.status==='submitted'&&isRecheckPool}
function draftComplete(){return state.draft.length===5&&state.draft.every(s=>Boolean(s.verdict))}
function cacheDraft(requestedSubmit=false){if(!state.item||submittedReadonly())return null;const old=offlineRecord()||{},x=state.item,itemSnapshot={event_id:x.event_id,source_ordinal:x.source_ordinal,page_id:x.page_id,request_id:x.request_id,image_ref:x.image_ref,parse_status:x.parse_status,parsed_slots:x.parsed_slots,duplicate_request_id:x.duplicate_request_id,status:x.status,version:x.version,slots:x.slots||[]};const revising=x.status==='submitted';const record={eventId:x.event_id,reviewer:state.reviewer,baseVersion:old.baseVersion??x.version,itemSnapshot,latestSlots:state.draft.map(v=>({...v})),operation:old.operation||null,requestedSubmit:Boolean(old.requestedSubmit||requestedSubmit||revising),revising:Boolean(old.revising||revising),updatedAt:new Date().toISOString()};writeOffline(record);return record}
function sameSlots(a,b){return JSON.stringify(a)===JSON.stringify(b)}
function latestOfflineForReviewer(){return Object.values(readOffline()).filter(x=>x?.reviewer===state.reviewer).sort((a,b)=>String(b.updatedAt).localeCompare(String(a.updatedAt)))[0]||null}
function sync(text,kind='pending'){const el=$('#sync-status');el.textContent=text;el.className='metric sync '+kind}
function showError(message=''){const el=$('#message');if(el)el.textContent=message}
async function refreshSummary(){state.summary=await api('api/summary');renderHeader()}
function renderHeader(){const x=state.item,done=state.summary.human_completed_groups??state.summary.statuses?.submitted??0,total=state.summary.total||0,ordinal=x?.source_ordinal||0,batch=selectedBatch||Math.max(1,Math.ceil(ordinal/1000)),label=caseKind==='goodcase'?`Goodcase 二次复核 · 第 ${batch} 批`:caseKind==='badcase'?`Badcase 二次复核 · 第 ${batch} 批`:caseKind==='unknown'?`Unknown 二次复核 · 第 ${batch} 批`:caseKind==='wrong'?`Badcase 专项复标 · 第 ${batch} 批`:`batch-${String(batch).padStart(2,'0')}`;$('#title').textContent=`5-SLOT 人工标注工作台 · ${label}`;$('#progress').textContent=x?`第 ${ordinal.toLocaleString()} / ${total.toLocaleString()} 组 · 已完成 ${done.toLocaleString()}`:`共 ${total.toLocaleString()} 组`;if(batchMode){$('#group-jump').min=1;$('#group-jump').max=Math.max(1,Math.ceil(total/1000));if(!$('#group-jump').value)$('#group-jump').value=batch}else{$('#group-jump').min=batchStart;$('#group-jump').max=Math.min(batchEnd,total)||1}}
function draftFrom(item){return Array.from({length:5},(_,i)=>{const old=(item.slots||[]).find(x=>x.slot===i+1)||{};return {verdict:old.verdict||null,revised_r:old.revised_r||'',revised_h:old.revised_h,reason_code:old.reason_code||'',note:old.note||''}})}
function recheckDraftFrom(item){return Array.from({length:5},(_,i)=>{const old=(item.rechecks||[]).find(x=>x.slot===i+1&&(!isRecheckPool||x.pool===caseKind))||{};return {verdict:old.verdict||null,note:old.note||''}})}
async function claim(item){if(item.status==='submitted')return item;return api('api/claims',{method:'POST',body:JSON.stringify({event_id:item.event_id,idempotency_key:key()})})}
async function loadItem(item){if((state.dirty||(state.item?.status!=='submitted'&&draftComplete()))&&!(await save(true)))throw new Error('当前标注尚未完整保存');state.item=await claim(item);state.draft=draftFrom(state.item);state.recheckDraft=recheckDraftFrom(state.item);state.focus=0;state.dirty=false;render();sync(state.item.status==='submitted'?(submittedReadonly()?'本组已完成，可逐个 SLOT 二次复核':'本组已完成，可直接修改后重新保存'):'服务器已同步','ok')}
async function openOrdinal(ordinal){try{sync('正在读取…');const item=await api(`api/by-ordinal?ordinal=${encodeURIComponent(ordinal)}`);await loadItem(item)}catch(err){sync('读取失败','offline');showError(err.message)}}
async function openRecheckPick(){if(state.recheckSaving){sync('当前 SLOT 正在保存，请稍后切组…');return}try{sync(`正在第 ${selectedBatch} 批随机抽取未完成复核的数据…`);const query=new URLSearchParams({pool:caseKind,mode:'random',start:String(batchStart),end:String(batchEnd)});const item=await api('api/recheck-pick?'+query);await loadItem(item)}catch(err){sync('当前批次暂无可复核数据','offline');showError(err.message)}}
async function openRandomSpecial(){try{if(state.dirty&&!(await save(true)))return;const batch=selectedBatch||Math.max(1,Math.ceil((state.item?.source_ordinal||1)/1000)),start=(batch-1)*1000+1,end=batch*1000;sync(`正在第 ${batch} 批随机抽取 Badcase…`);const query=new URLSearchParams({kind:caseKind,start:String(start),end:String(end)});const item=await api('api/random-special-item?'+query);await loadItem(item)}catch(err){sync('随机抽取失败','offline');showError(err.message)}}
function navQuery(ordinal,direction){return new URLSearchParams({ordinal:String(ordinal),direction:String(direction),status:caseKind?'':$('#filter').value,start:String(batchStart),end:String(batchEnd),kind:caseKind})}
async function openFirstUnreviewed(){try{if(state.dirty&&!(await save(true)))return;sync('正在筛选…');const item=await api('api/navigate?'+navQuery(batchStart-1,1));await loadItem(item)}catch(err){sync('暂无符合条件的数据','offline');showError('当前批次暂无符合条件的数据')}}
async function move(direction){try{if(!state.item)return;if((state.dirty||(state.item.status!=='submitted'&&draftComplete()))&&!(await save(true)))return;sync('正在切组…');const item=await api('api/navigate?'+navQuery(state.item.source_ordinal,direction));await loadItem(item)}catch(err){sync('没有更多符合条件的组','offline');showError(err.message.includes('保存')?err.message:'没有更多符合当前筛选条件的组')}}
function render(){renderHeader();const x=state.item;if(!x){$('#app').innerHTML='<div class="empty">没有可显示的数据</div>';return}$('#app').innerHTML=`<article class="card"><section class="visual"><img id="image" referrerpolicy="no-referrer" alt="当前整页题目"><div class="caption">组 #${x.source_ordinal} · page ${esc(x.page_id)} · request ${esc(x.request_id)}</div><div class="source-badges"><span class="badge">${esc(x.parse_status)}</span>${x.duplicate_request_id?'<span class="badge">重复 request_id（已保留）</span>':''}${x.status==='submitted'?'<span class="badge">已完成</span>':''}</div><details><summary>查看模型完整原文</summary><pre>${esc(x.model_raw_content)}</pre></details></section><section class="slots" id="slots"></section></article><div id="message" class="error"></div>`;$('#image').src=x.image_ref;renderSlots()}
function correctionInvalid(s){return Boolean(s.revised_r)!=(s.revised_h!=null)}
function reviewMetaMarkup(i){const review=(state.item?.slots||[]).find(x=>x.slot===i+1);if(!review?.verdict||!review?.updated_by)return '<div class="review-meta">尚无人工标注记录</div>';const stamp=review.updated_at?new Date(review.updated_at).toLocaleString('zh-CN',{hour12:false}):'';return `<div class="review-meta">最后标注：<b>${esc(review.updated_by)}</b>${stamp?`<span>${esc(stamp)}</span>`:''}</div>`}
function recheckEligible(i){if(!isRecheckPool)return true;const r=(state.item?.slots||[]).find(x=>x.slot===i+1)||{};if(caseKind==='goodcase')return r.verdict==='correct';if(caseKind==='badcase')return r.verdict==='wrong'&&Boolean(String(r.revised_r||'').trim());return r.verdict==='unsure'}
function slotRecheckMarkup(i){if(!isRecheckPool||state.item.status!=='submitted'||!recheckEligible(i))return '';const d=state.recheckDraft[i]||{verdict:null,note:''},disabled=state.recheckSaving?'disabled':'';return `<div class="slot-recheck"><div class="slot-recheck-copy"><b>SLOT ${i+1} 二次复核</b><span>点击准确或不准即自动保存；备注修改后离开输入框自动保存。</span></div><div class="recheck-actions"><button data-rv="accurate" class="${d.verdict==='accurate'?'active accurate':''}" ${disabled}>人工原标准确</button><button data-rv="inaccurate" class="${d.verdict==='inaccurate'?'active inaccurate':''}" ${disabled}>发现人工标注不准</button><input data-rnote value="${esc(d.note)}" placeholder="该 SLOT 复核备注（可选）" ${disabled}></div></div>`}
function renderSlots(){const parsed=state.item.parsed_slots||[],readonly=submittedReadonly(),indices=state.draft.map((_,i)=>i).filter(i=>recheckEligible(i));$('#slots').innerHTML=indices.map(i=>{const s=state.draft[i],m=parsed[i]||{};return `<section class="slot ${i===state.focus?'focus':''}" data-slot="${i}"><div class="head"><b>SLOT ${i+1}</b><span class="badge">Model</span><span class="uid">${esc(state.item.event_id.slice(0,10))}-${i+1}</span></div>${slotRecheckMarkup(i)}<div class="verdicts"><div class="verdict ${s.verdict==='correct'?'correct':''}"><h3>模型结果</h3><div class="result">${esc(m.r||'空结果')}</div><div class="actions"><button data-v="correct" class="${s.verdict==='correct'?'active correct':''}" ${readonly?'disabled':''}>正确</button><button data-v="wrong" class="${s.verdict==='wrong'?'active wrong':''}" ${readonly?'disabled':''}>错误</button></div></div><div class="verdict ${s.verdict==='wrong'?'wrong':''}"><h3>人工复核 / 修正</h3>${s.verdict==='wrong'?`<div class="correction"><input data-k="revised_r" value="${esc(s.revised_r)}" placeholder="修正后的结果（可选）" ${readonly?'disabled':''}><textarea data-k="note" placeholder="备注（可选）" ${readonly?'disabled':''}>${esc(s.note)}</textarea></div>`:`<div class="hint">${s.verdict==='correct'?'已确认模型结果正确，无需修正。':s.verdict==='unsure'?'该 SLOT 为 Unknown 人工标注。':'该 SLOT 尚未标注，请选择一个结论。'}</div>`}${reviewMetaMarkup(i)}</div></div><div class="flag-buttons"><button class="flag-blurred ${s.verdict==='unsure'&&s.reason_code==='image_blurred'?'active':''}" data-flag="image_blurred" ${readonly?'disabled':''}>图片残缺或模糊</button><button class="flag-no-handwriting ${s.verdict==='unsure'&&s.reason_code==='no_handwriting'?'active':''}" data-flag="no_handwriting" ${readonly?'disabled':''}>无手写作答</button><button class="flag-unjudgable ${s.verdict==='unsure'&&s.reason_code==='ungradable'?'active':''}" data-flag="ungradable" ${readonly?'disabled':''}>无法判断</button></div></section>`}).join('');bindSlotEvents();bindRecheckEvents()}
function bindSlotEvents(){document.querySelectorAll('.slot').forEach(el=>{const i=+el.dataset.slot;el.onclick=e=>{if(e.target.closest('button,input,select,textarea,a'))return;state.focus=i;renderSlots()};el.querySelectorAll('[data-v]').forEach(b=>b.onclick=e=>{e.stopPropagation();setVerdict(i,b.dataset.v)});el.querySelectorAll('[data-flag]').forEach(b=>b.onclick=e=>{e.stopPropagation();setUnsure(i,b.dataset.flag)});el.querySelectorAll('[data-k]').forEach(inp=>{inp.onclick=e=>e.stopPropagation();inp.oninput=()=>update(i,inp.dataset.k,inp.dataset.k==='revised_h'?(inp.value===''?null:+inp.value):inp.value);inp.onchange=inp.oninput})})}
function bindRecheckEvents(){document.querySelectorAll('.slot').forEach(el=>{const i=+el.dataset.slot;el.querySelectorAll('[data-rv]').forEach(b=>b.onclick=async e=>{e.stopPropagation();if(state.recheckSaving)return;const previous={...state.recheckDraft[i]};state.recheckDraft[i].verdict=b.dataset.rv;renderSlots();await saveRecheck(i,previous)});const note=el.querySelector('[data-rnote]');if(note){note.onclick=e=>e.stopPropagation();note.oninput=()=>{state.recheckDraft[i].note=note.value};note.onchange=()=>setTimeout(async()=>{const saved=recheckDraftFrom(state.item)[i],current=state.recheckDraft[i];if(current.verdict&&!state.recheckSaving&&(current.verdict!==saved.verdict||current.note!==saved.note))await saveRecheck(i,saved)},300)}})}
function markDirty(){state.dirty=true;state.editRevision+=1;const complete=draftComplete();try{cacheDraft(complete);sync(complete?'本组已完整，正在自动提交…':'已保存到本机，正在同步…')}catch{sync('本机缓存失败，请立即检查浏览器存储','offline')}clearTimeout(state.timer);state.timer=setTimeout(()=>save(complete),700)}
function setVerdict(i,v){if(submittedReadonly())return;state.draft[i].verdict=v;state.draft[i].reason_code='';if(v!=='wrong'){state.draft[i].revised_r='';state.draft[i].revised_h=null}state.focus=i;markDirty();renderSlots()}
function setUnsure(i,reason){if(submittedReadonly())return;Object.assign(state.draft[i],{verdict:'unsure',reason_code:reason,revised_r:'',revised_h:null});state.focus=i;markDirty();renderSlots()}
function update(i,k,v){state.draft[i][k]=v;if(k==='revised_r')state.draft[i].revised_h=v?0:null;markDirty()}
function validCorrection(){return true}
async function save(forceSubmit=false){
  if(!state.item||submittedReadonly())return true;
  let record=offlineRecord();
  if(!state.dirty&&!record&&!forceSubmit)return true;
  if(state.saving){
    sync('正在保存，完成后自动继续…');
    if(state.saveDone)await state.saveDone;
    if(!state.dirty&&!offlineRecord())return true;
    return save(forceSubmit)
  }
  clearTimeout(state.timer);state.saving=true;
  state.saveDone=new Promise(resolve=>{state.resolveSave=resolve});
  const eventId=state.item.event_id,revision=state.editRevision,revising=state.item.status==='submitted';
  try{
    record=cacheDraft(forceSubmit)||record;
    if(!record.operation){
      const complete=record.latestSlots.every(s=>s.verdict);
      record.latestSlots=record.latestSlots.map(x=>({...x,revised_h:x.revised_r?0:null}));
      record.operation={idempotencyKey:key(),version:state.item.version,slots:record.latestSlots.map(x=>({...x})),submit:Boolean(record.requestedSubmit&&complete)};
      writeOffline(record)
    }
    const op=record.operation;
    const item=await api(`api/items/${eventId}/${op.submit?'submit':'draft'}`,{method:'POST',body:JSON.stringify({version:op.version,slots:op.slots,idempotency_key:op.idempotencyKey})});
    if(state.item?.event_id!==eventId)return true;
    state.item={...state.item,...item};
    const latest=offlineRecord(eventId)||record;
    latest.latestSlots=latest.latestSlots.map(x=>({...x,revised_h:x.revised_r?0:null}));
    const changed=!sameSlots(latest.latestSlots,op.slots),needsSubmit=Boolean(latest.requestedSubmit&&!op.submit&&latest.latestSlots.every(s=>s.verdict));
    if(op.submit){
      removeOffline(eventId);state.dirty=false;
      sync(revising?'修改已自动保存':'本组已完成，本地缓存已清除','ok');
      render();refreshSummary().catch(()=>{});return true
    }
    if(changed||needsSubmit){
      latest.operation=null;latest.baseVersion=item.version;writeOffline(latest);
      state.draft=latest.latestSlots.map(x=>({...x}));state.dirty=true;
      sync('服务器已收到一版，正在续传最新修改…');clearTimeout(state.timer);
      state.timer=setTimeout(()=>save(needsSubmit),100);return !forceSubmit
    }
    removeOffline(eventId);state.dirty=state.editRevision!==revision;
    sync(state.dirty?'仍有修改待同步':'服务器已同步，本地缓存已清除',state.dirty?'pending':'ok');renderHeader();return true
  }catch(err){
    try{cacheDraft(forceSubmit)}catch{}
    if(err.status===409){
      const stale=offlineRecord(eventId);archiveConflict(stale);removeOffline(eventId);state.dirty=false;
      try{
        const latest=await api(`api/items/${eventId}`);
        if(state.item?.event_id===eventId){state.item=latest;state.draft=draftFrom(latest);state.recheckDraft=recheckDraftFrom(latest);render()}
      }catch{}
      sync('已自动切换到服务器最新版','ok');
      showError('检测到旧版本草稿，已在本机归档并停止重复提交；现在可直接继续标注。');
      return true
    }
    sync('网络异常：已离线保存，恢复后自动上传','offline');
    showError('当前标注已安全保存在本机，无需重复填写。');return false
  }finally{
    state.saving=false;state.resolveSave?.();state.saveDone=null;state.resolveSave=null;
    if(state.dirty||offlineRecord(eventId)){clearTimeout(state.timer);state.timer=setTimeout(()=>save(Boolean(offlineRecord(eventId)?.requestedSubmit)),5000)}
  }
}
async function saveRecheck(i,previous=null){const d={...state.recheckDraft[i]};if(!d?.verdict){sync(`请选择 SLOT ${i+1} 的二次复核结论`,'offline');return false}if(state.recheckSaving)return false;state.recheckSaving=true;renderSlots();try{sync(`正在自动保存 SLOT ${i+1} 二次复核…`);const item=await api(`api/items/${state.item.event_id}/recheck`,{method:'POST',body:JSON.stringify({slot:i+1,verdict:d.verdict,note:d.note,pool:caseKind,idempotency_key:key()})});state.item={...state.item,...item};state.recheckDraft=recheckDraftFrom(item);sync(`SLOT ${i+1} 二次复核已自动保存`,'ok');return true}catch(err){if(previous)state.recheckDraft[i]={...previous};sync(`SLOT ${i+1} 二次复核保存失败，已恢复原状态`,'offline');showError(err.message);return false}finally{state.recheckSaving=false;renderSlots()}}
async function resumeOffline(){const record=latestOfflineForReviewer();if(!record)return false;try{sync('正在恢复本机草稿…');let item=await api(`api/items/${encodeURIComponent(record.eventId)}`);if(item.status==='submitted'&&!record.revising){if(record.operation?.submit)removeOffline(record.eventId);else sync('本机草稿与已提交版本冲突，草稿仍保留','offline');state.item=item;state.draft=draftFrom(item);state.recheckDraft=recheckDraftFrom(item);render();return true}if(item.status!=='submitted')item=await claim(item);state.item=item;state.draft=(record.latestSlots||draftFrom(item)).map(x=>({...x}));state.recheckDraft=recheckDraftFrom(item);state.focus=0;state.dirty=true;render();sync('已恢复本机草稿，正在自动上传…');setTimeout(()=>save(Boolean(record.requestedSubmit)),50);return true}catch(err){if(record.itemSnapshot){state.item=record.itemSnapshot;state.draft=(record.latestSlots||draftFrom(record.itemSnapshot)).map(x=>({...x}));state.recheckDraft=recheckDraftFrom(record.itemSnapshot);state.focus=0;state.dirty=true;render()}sync('离线草稿已恢复，等待网络后自动上传','offline');return Boolean(record.itemSnapshot)}}
$('#logout').onclick=async()=>{try{await api('api/auth/logout',{method:'POST',body:'{}'})}finally{location.replace('login.html')}};
$('#jump').onclick=()=>{if(batchMode){const maxBatch=Math.max(1,Math.ceil(state.summary.total/1000)),batch=Math.min(Math.max(1,+$('#group-jump').value||1),maxBatch),query=new URLSearchParams(location.search);query.set('kind',caseKind);query.set('batch',String(batch));location.href='review.html?'+query;return}const ordinal=Math.min(Math.max(1,+$('#group-jump').value||1),state.summary.total);return openOrdinal(ordinal)};
$('#random-recheck').onclick=()=>caseKind==='wrong'?openRandomSpecial():openRecheckPick();
$('#group-jump').onkeydown=e=>{if(e.key==='Enter')$('#jump').click()};
$('#prev').onclick=()=>move(-1);$('#next').onclick=()=>move(1);$('#filter').onchange=()=>openFirstUnreviewed();
$('#all-correct').onclick=()=>{if(!state.item||submittedReadonly())return;state.draft.forEach(x=>Object.assign(x,{verdict:'correct',revised_r:'',revised_h:null,reason_code:''}));markDirty();renderSlots()};
$('#save-now').onclick=async()=>{const ok=await save(true);if(ok)sync(state.item?.status==='submitted'?'本组已完成':'当前进度已保存到服务器','ok')};
document.addEventListener('keydown',e=>{if(!state.item||['INPUT','TEXTAREA','SELECT'].includes(e.target.tagName))return;if(e.key==='ArrowLeft'){e.preventDefault();move(-1)}else if(e.key==='ArrowRight'){e.preventDefault();move(1)}else if(e.key==='1')setVerdict(state.focus,'correct');else if(e.key==='2')setVerdict(state.focus,'wrong');else if(e.key==='4')setUnsure(state.focus,'no_handwriting');else if(e.key==='5')setUnsure(state.focus,'ungradable')});
setInterval(()=>{if((state.dirty||offlineRecord())&&!state.saving)save(Boolean(offlineRecord()?.requestedSubmit))},5000);addEventListener('online',()=>{if(state.dirty||offlineRecord())save(Boolean(offlineRecord()?.requestedSubmit))});addEventListener('beforeunload',e=>{if(state.dirty){e.preventDefault();e.returnValue=''}});
(async()=>{try{await requireSession();if(caseKind){$('#filter').value='submitted';$('#filter').disabled=true;$('#filter').title='专项复核池由入口决定'}if(isRecheckPool){$('#random-recheck').hidden=false;$('#random-recheck').textContent='当前批次随机复核';$('#group-jump').placeholder='批次号';$('#group-jump').setAttribute('aria-label','复核批次号');$('#jump').textContent='进入批次';$('#all-correct').hidden=true;$('#save-now').hidden=true;const target=caseKind==='goodcase'?'人工标为正确的 SLOT':caseKind==='badcase'?'人工标为错误且已有修正的 SLOT':'人工标为模糊、无手写或无法判断的 SLOT';$('#subtitle').textContent=`当前只复核${target}；输入批次号进入对应的一千组范围，随机复核和上一组/下一组均限定在当前批次。`}if(caseKind==='wrong'){$('#random-recheck').hidden=false;$('#random-recheck').textContent='当前批次随机抽取';$('#group-jump').placeholder='批次号';$('#group-jump').setAttribute('aria-label','Badcase 批次号');$('#jump').textContent='进入批次';$('#subtitle').textContent='Badcase 专项复标：按每批一千组选择批次，可在当前批次顺序切换或随机抽取；修改会自动保存，并显示最后标注人。'}const resumed=await resumeOffline();try{await refreshSummary()}catch(err){if(!resumed)throw err}if(!resumed)await openFirstUnreviewed()}catch(err){sync('连接失败，本机草稿会继续保留','offline');$('#app').innerHTML=`<div class="empty">连接服务器失败：${esc(err.message)}</div>`}})();
