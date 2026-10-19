export function activeNavigationTarget(positions,threshold=150,{atBottom=false,viewportHeight=Infinity}={}){
  let active='#overview',closest=-Infinity;
  for(const {hash,top} of positions)if(hash!=='#overview'&&Number.isFinite(top)&&(atBottom?top<viewportHeight:top<=threshold)&&top>=closest){active=hash;closest=top;}
  return active;
}
export function revealNavigationTarget(document,hash){
  const target=document.getElementById(hash.slice(1));if(!target)return null;
  for(let element=target;element;element=element.parentElement)if(element.tagName==='DETAILS')element.open=true;
  target.scrollIntoView({block:'start',behavior:'instant'});
  const focus=target.querySelector(target.tagName==='DETAILS'?':scope > summary':'h2,h1')??target;
  focus.setAttribute('tabindex','-1');focus.focus({preventScroll:true});
  return target;
}
export function showWorkspacePage(document,hash){
  const target=document.getElementById(hash.slice(1));
  const view=target?.closest('[data-workspace-page]')?.dataset.workspacePage??'overview';
  for(const page of document.querySelectorAll('[data-workspace-page]'))page.classList.toggle('current-page',page.dataset.workspacePage===view);
  document.body.dataset.workspaceView=view;
  return '#'+view;
}
export function setupNavigation(document,window,{onNavigate=()=>{}}={}){
  const sidebar=document.getElementById('sidebar'),toggle=document.getElementById('menu-toggle');
  const close=document.getElementById('menu-close'),backdrop=document.getElementById('menu-backdrop');
  const workspace=document.querySelector('.workspace'),media=window.matchMedia('(max-width:960px)');
  const links=[...document.querySelectorAll('.sidebar-nav a')];
  let opened=false;
  function setOpen(value,restoreFocus=true){
    opened=media.matches&&value;
    document.body.classList.toggle('sidebar-open',opened);
    toggle.setAttribute('aria-expanded',String(opened));
    sidebar.inert=media.matches&&!opened;workspace.inert=opened;backdrop.hidden=!opened;
    if(opened)close.focus();else if(restoreFocus&&media.matches)toggle.focus();
  }
  function activate(hash){for(const link of links){if(link.hash===hash)link.setAttribute('aria-current','location');else link.removeAttribute('aria-current');}}
  toggle.addEventListener('click',()=>setOpen(!opened));
  close.addEventListener('click',()=>setOpen(false));backdrop.addEventListener('click',()=>setOpen(false));
  document.addEventListener('keydown',event=>{
    if(!opened)return;
    if(event.key==='Escape'){event.preventDefault();setOpen(false);}
    if(event.key==='Tab'){
      const items=[...sidebar.querySelectorAll('a,button')],first=items[0],last=items.at(-1);
      if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}
      else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}
    }
  });
  function restoreLocation(){
    const hash=window.location.hash||'#overview';
    if(opened)setOpen(false,false);
    const active=showWorkspacePage(document,hash);activate(active);onNavigate(active,hash);
    revealNavigationTarget(document,document.getElementById(hash.slice(1))?hash:active);
    if(hash===active||!document.getElementById(hash.slice(1)))window.scrollTo({top:0,behavior:'instant'});
  }
  function navigate(hash){
    if(window.location.hash!==hash)window.history.pushState(null,'',hash);
    restoreLocation();
  }
  document.addEventListener('click',event=>{
    const link=event.target.closest?.('a[href^="#"]');
    if(!link||!document.getElementById(link.hash.slice(1))||event.defaultPrevented||event.button>0||event.ctrlKey||event.metaKey||event.altKey||event.shiftKey)return;
    event.preventDefault();
    navigate(link.hash);
  });
  window.addEventListener('hashchange',restoreLocation);
  window.addEventListener('popstate',restoreLocation);
  media.addEventListener('change',()=>setOpen(false,false));
  setOpen(false,false);showWorkspacePage(document,window.location.hash||'#overview');
  return {restoreLocation,navigate};
}

// Shared request feedback belongs to the application shell, outside page navigation.
export function setupActionFeedback(document,{now=Date.now,timers=globalThis}={}){
  const root=document.getElementById('action-feedback'),title=document.getElementById('action-feedback-title');
  const detail=document.getElementById('action-feedback-detail'),close=document.getElementById('action-feedback-close');
  const tasks=new Map(),buttons=new Set();let trigger=null,sequence=0,ticker=null,hideTimer=null;
  function remember(button){trigger=button?{button,at:now()}:null;}
  document.addEventListener('click',event=>remember(event.target.closest?.('button,input[type="submit"]')),true);
  document.addEventListener('submit',event=>remember(event.submitter),true);
  function takeTrigger(){const captured=trigger;trigger=null;return captured&&now()-captured.at<1000?captured.button:null;}
  function restoreAttribute(button,name,value){if(value===null)button.removeAttribute(name);else button.setAttribute(name,value);}
  function cancelHide(){if(hideTimer!==null)timers.clearTimeout(hideTimer);hideTimer=null;}
  function seconds(task){return Math.max(0,Math.floor((now()-task.startedAt)/1000));}
  function showPending(){
    const task=[...tasks.values()].at(-1);if(!task)return;
    root.hidden=false;root.dataset.state='pending';title.textContent=task.label;
    detail.textContent=(task.detail?task.detail+' · ':'처리하고 있습니다. · ')+seconds(task)+'초 경과';close.hidden=true;
  }
  function begin(key,label,description,button){
    cancelHide();
    const task={label,detail:description,startedAt:now(),button};
    if(button){
      task.attributes={busy:button.getAttribute('aria-busy'),loading:button.getAttribute('data-loading')};
      buttons.add(button);button.setAttribute('aria-busy','true');button.setAttribute('data-loading','true');
    }
    tasks.set(key,task);showPending();if(ticker===null)ticker=timers.setInterval(showPending,1000);
    return task;
  }
  function finish(key,failed){
    const task=tasks.get(key);if(!task)return;tasks.delete(key);
    if(task.button){
      buttons.delete(task.button);restoreAttribute(task.button,'aria-busy',task.attributes.busy);restoreAttribute(task.button,'data-loading',task.attributes.loading);
    }
    if(tasks.size){showPending();return;}
    timers.clearInterval(ticker);ticker=null;cancelHide();root.hidden=false;root.dataset.state=failed?'error':'success';
    title.textContent=failed?'요청 처리 실패':'요청 처리 완료';
    detail.textContent=task.label.replace(/\s*중[…\.]*$/u,'')+' · '+seconds(task)+'초. '+(failed?'화면의 오류 안내와 현재 상태를 확인하세요.':'화면의 결과와 실행 상태를 확인하세요.');
    close.hidden=false;if(!failed)hideTimer=timers.setTimeout(()=>{root.hidden=true;hideTimer=null;},5000);
  }
  close.addEventListener('click',()=>{if(!tasks.size){cancelHide();root.hidden=true;}});
  return {
    async run(label,work){
      const button=takeTrigger();if(button&&buttons.has(button))return;
      const key=++sequence;begin(key,label,'',button);
      try{const result=await work();finish(key,false);return result;}catch(error){finish(key,true);throw error;}
    },
    reflect(key,{busy,label='처리 중…',detail:description='',failed=false}={}){
      if(busy){
        const task=tasks.get(key);
        if(task){task.label=label;task.detail=description;showPending();}
        else{const button=takeTrigger();begin(key,label,description,button&&!buttons.has(button)?button:null);}
      }else finish(key,failed);
    }
  };
}
