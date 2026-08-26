const form=document.querySelector('#login-form');
const button=document.querySelector('#login-button');
const message=document.querySelector('#login-message');
const params=new URLSearchParams(location.search);
function safeNext(){const next=params.get('next')||'dashboard.html';return /^(dashboard\.html|review\.html|leaderboard\.html)(\?.*)?$/.test(next)?next:'dashboard.html'}
async function existingSession(){try{const r=await fetch('api/session',{cache:'no-store'}),v=await r.json();if(v.authenticated)location.replace(safeNext())}catch{}}
form.addEventListener('submit',async event=>{event.preventDefault();message.textContent='';button.disabled=true;button.textContent='正在验证…';try{const response=await fetch('api/auth/bind',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({display_name:document.querySelector('#display-name').value,invite_code:document.querySelector('#invite-code').value})});const value=await response.json();if(!response.ok)throw new Error(value.detail||value.error||'登录失败');location.replace(safeNext())}catch(error){message.textContent=error.message;button.disabled=false;button.textContent='进入标注平台'}});
existingSession();
