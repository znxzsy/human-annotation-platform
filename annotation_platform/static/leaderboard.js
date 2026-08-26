const $=selector=>document.querySelector(selector);
const fmt=value=>Number(value||0).toLocaleString('zh-CN');
let SESSION=null,DATA=null,SORT='accuracy',MIN_ANNOTATED=200,DAILY_MIN=100;

function escapeHtml(value){return String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]))}
function identity(item){return `<span class="identity ${item.is_real_name?'':'legacy'}">${item.is_real_name?'实名':'历史匿名'}</span>`}
function shiftDate(value,days){const date=new Date(`${value}T12:00:00+08:00`);date.setUTCDate(date.getUTCDate()+days);return date.toISOString().slice(0,10)}

async function requireSession(){
  const response=await fetch('api/session',{cache:'no-store'}),value=await response.json();
  if(!value.authenticated){location.replace('login.html?next=leaderboard.html');throw new Error('请先登录')}
  SESSION=value;$('#reviewer').textContent=value.reviewer;
}

function sortedItems(){
  const rows=[...(DATA?.items||[])].filter(item=>item.annotated_slots>=MIN_ANNOTATED);
  if(SORT==='volume'){
    rows.sort((a,b)=>b.annotated_slots-a.annotated_slots||b.annotated_groups-a.annotated_groups||(b.accuracy??-1)-(a.accuracy??-1)||a.reviewer.localeCompare(b.reviewer,'zh-CN'));
  }else{
    rows.sort((a,b)=>(b.accuracy??-1)-(a.accuracy??-1)||b.reviewed_slots-a.reviewed_slots||b.annotated_slots-a.annotated_slots||a.reviewer.localeCompare(b.reviewer,'zh-CN'));
  }
  return rows;
}

function renderRows(){
  const rows=sortedItems(),all=DATA?.items||[];
  document.querySelectorAll('#annotation-sorter [data-sort]').forEach(button=>button.classList.toggle('active',button.dataset.sort===SORT));
  document.querySelectorAll('#annotation-sorter [data-min-annotated]').forEach(button=>button.classList.toggle('active',Number(button.dataset.minAnnotated)===MIN_ANNOTATED));
  const hidden=all.length-rows.length;$('#annotation-filter-note').textContent=hidden?` 已隐藏 ${fmt(hidden)} 人。`:'';
  $('#rows').innerHTML=rows.length?rows.map((item,index)=>`<tr><td><span class="rank">${index+1}</span></td><td class="name">${escapeHtml(item.reviewer)}${identity(item)}</td><td>${fmt(item.annotated_slots)}</td><td>${fmt(item.annotated_groups)}</td><td>${fmt(item.reviewed_slots)}</td><td>${fmt(item.accurate_slots)}</td><td class="bad">${fmt(item.inaccurate_slots)}</td><td>${item.accuracy==null?'<span class="muted">待复核</span>':`<span class="rate">${item.accuracy.toFixed(2)}%</span>`}</td><td>${fmt(item.goodcase_reviewed)} / ${fmt(item.badcase_reviewed)} / ${fmt(item.unknown_reviewed)}</td></tr>`).join(''):`<tr><td colspan="9" class="empty">当前筛选下暂无人员，可点击“显示全部”</td></tr>`;
}

function renderDailyRows(){
  const all=DATA?.daily_items||[],rows=all.filter(item=>item.reviewed_slots>=DAILY_MIN);
  document.querySelectorAll('[data-daily-min]').forEach(button=>button.classList.toggle('active',Number(button.dataset.dailyMin)===DAILY_MIN));
  const hidden=all.length-rows.length;$('#daily-filter-note').textContent=hidden?`已隐藏 ${fmt(hidden)} 名复核量不足 ${DAILY_MIN} 的人员。`:'';
  $('#daily-rows').innerHTML=rows.length?rows.map((item,index)=>`<tr><td><span class="rank">${index+1}</span></td><td class="name">${escapeHtml(item.reviewer)}${identity(item)}</td><td><span class="volume">${fmt(item.reviewed_slots)}</span></td><td>${fmt(item.reviewed_groups)}</td><td>${fmt(item.accurate_slots)}</td><td class="bad">${fmt(item.inaccurate_slots)}</td><td>${fmt(item.goodcase_reviewed)} / ${fmt(item.badcase_reviewed)} / ${fmt(item.unknown_reviewed)}</td></tr>`).join(''):`<tr><td colspan="7" class="empty">该日期没有达到 ${DAILY_MIN} SLOT 的复核人员，可点击“显示全部”</td></tr>`;
}

function renderDailyReconciliation(){
  const cumulative=DATA?.cumulative_unique_rechecked_slots??DATA?.reviewed_slots??0;
  const unique=DATA?.daily_unique_reviewed_slots??0;
  const personSlots=DATA?.daily_review_person_slots??DATA?.daily_reviewed_slots??0;
  const duplicates=DATA?.daily_cross_reviewer_duplicates??Math.max(0,personSlots-unique);
  if(DATA?.selected_date===DATA?.today){
    const prior=Math.max(0,cumulative-unique);
    $('#daily-reconcile').textContent=`今日唯一复核 ${fmt(unique)} + 今日以前保留 ${fmt(prior)} = 当前累计唯一复核 ${fmt(cumulative)}；人员工作量 ${fmt(personSlots)}，其中跨人员重复 ${fmt(duplicates)}。`;
  }else{
    $('#daily-reconcile').textContent=`${DATA?.selected_date||'所选日期'}唯一复核 ${fmt(unique)}；人员工作量 ${fmt(personSlots)}，其中跨人员重复 ${fmt(duplicates)}；当前累计唯一复核 ${fmt(cumulative)}。`;
  }
}

async function load(){
  const selected=$('#review-date').value;
  const response=await fetch(`api/reviewer-leaderboard${selected?`?date=${encodeURIComponent(selected)}`:''}`,{cache:'no-store'});
  if(response.status===401){location.replace('login.html?next=leaderboard.html');return}
  DATA=await response.json();if(!response.ok)throw new Error(DATA.error||response.status);
  $('#review-date').value=DATA.selected_date;$('#review-date').max=DATA.today;
  $('#date-next').disabled=DATA.selected_date>=DATA.today;
  $('#annotators').textContent=fmt(DATA.real_annotators);$('#reviewed').textContent=fmt(DATA.reviewed_slots);
  $('#accurate').textContent=fmt(DATA.accurate_slots);$('#inaccurate').textContent=fmt(DATA.inaccurate_slots);
  $('#daily-unique').textContent=fmt(DATA.daily_unique_reviewed_slots);$('#daily-person').textContent=fmt(DATA.daily_review_person_slots);
  $('#daily-duplicates').textContent=fmt(DATA.daily_cross_reviewer_duplicates);$('#daily-reviewers').textContent=fmt(DATA.daily_reviewers);
  renderDailyReconciliation();renderDailyRows();renderRows();$('#updated').textContent=`数据日期：${DATA.selected_date}（北京时间） · 最近刷新：${new Date().toLocaleTimeString('zh-CN',{hour12:false})}`;
}

document.querySelectorAll('#annotation-sorter [data-sort]').forEach(button=>button.onclick=()=>{SORT=button.dataset.sort;renderRows()});
document.querySelectorAll('#annotation-sorter [data-min-annotated]').forEach(button=>button.onclick=()=>{MIN_ANNOTATED=Number(button.dataset.minAnnotated);renderRows()});
document.querySelectorAll('[data-daily-min]').forEach(button=>button.onclick=()=>{DAILY_MIN=Number(button.dataset.dailyMin);renderDailyRows()});
$('#review-date').onchange=()=>load().catch(showLoadError);
$('#date-prev').onclick=()=>{$('#review-date').value=shiftDate($('#review-date').value,-1);load().catch(showLoadError)};
$('#date-next').onclick=()=>{$('#review-date').value=shiftDate($('#review-date').value,1);load().catch(showLoadError)};
$('#date-today').onclick=()=>{$('#review-date').value=DATA?.today||'';load().catch(showLoadError)};
function showLoadError(error){
  const message=`榜单加载失败：${escapeHtml(error.message)}`;
  $('#daily-rows').innerHTML=`<tr><td colspan="7" class="empty">${message}</td></tr>`;
  $('#rows').innerHTML=`<tr><td colspan="9" class="empty">${message}</td></tr>`;
}
$('#logout').onclick=async()=>{try{await fetch('api/auth/logout',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':SESSION?.csrf||''},body:'{}'})}finally{location.replace('login.html')}};
(async()=>{try{await requireSession();await load();setInterval(()=>load().catch(showLoadError),30000)}catch(error){showLoadError(error)}})();
