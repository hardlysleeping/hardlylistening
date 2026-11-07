/* Shared metadata labels; no inference or network inside render. */
window.Genres = (() => {
  let selected = [], taxonomy = [];
  let lastRows, byId = new Map();
  const matches = track => {
    if (!selected.length) return true;
    if (typeof libAll !== 'undefined' && lastRows !== libAll) {
      lastRows=libAll; byId=new Map(libAll.map(t=>[t.id,t]));
    }
    return selected.some(tag => (track.genres || byId.get(track.id)?.genres || []).includes(tag));
  };
  async function post(path, body) {
    const response = await fetch(path, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const data = await response.json();
    if (!response.ok) throw Error(data.error || 'Genre action failed');
    return data;
  }
  async function init() {
    const bar = document.querySelector('.libbar');
    if (!bar) return;
    const panel = document.createElement('details'); panel.className='genre-panel';
    const summary = document.createElement('summary'); summary.textContent='Genres (any selected / OR)'; panel.append(summary);
    const help=document.createElement('p'); help.textContent='Shared artist/title/album metadata estimates — not audio analysis. Untagged songs match only with no filter. Admin actions apply only to explicit IDs (maximum 25); corrections replace labels and protect them from AI.'; panel.append(help);
    const labels=document.createElement('div'); panel.append(labels);
    const status=document.createElement('p'); status.setAttribute('role','status'); panel.append(status);
    bar.after(panel);
    try {
      const response=await fetch('/api/genres'); const data=await response.json();
      if(!response.ok) throw Error(data.error || 'Genres unavailable');
      taxonomy=data.taxonomy;
      for(const tag of taxonomy){
        const label=document.createElement('label'); const input=document.createElement('input'); input.type='checkbox'; input.value=tag;
        input.onchange=()=>{selected=[...labels.querySelectorAll('input:checked')].map(n=>n.value); renderLibrary();};
        label.append(input,document.createTextNode(tag)); labels.append(label);
      }
      // Recheck on open: initial state may still be loading at window load.
      const adminPanel=document.createElement('div'); panel.append(adminPanel);
      const updateRole=()=>{adminPanel.hidden=!(typeof isAdmin==='function' && isAdmin());};
      panel.addEventListener('toggle',updateRole); updateRole();
      const ids=document.createElement('input'); ids.placeholder='Explicit track IDs, e.g. 12,34'; ids.setAttribute('aria-label','Track IDs for genre actions'); adminPanel.append(ids);
      const pick=document.createElement('select'); pick.multiple=true; pick.setAttribute('aria-label','Choose tracks for genre tagging');
      const fill=document.createElement('button'); fill.textContent='Load displayed tracks for selection';
      fill.onclick=()=>{pick.replaceChildren(); for(const t of currentRows){const option=document.createElement('option'); option.value=t.id; option.textContent=`${t.artist_name} — ${t.title} [${(t.genres||[]).join(', ')||'untagged'}]`; pick.append(option);}};
      pick.onchange=()=>{ids.value=[...pick.selectedOptions].map(o=>o.value).join(',');}; adminPanel.append(fill,pick);
      const tags=document.createElement('input'); tags.placeholder='Correction tags, comma-separated'; tags.setAttribute('aria-label','Replacement genre labels'); adminPanel.append(tags);
      for(const [text,path] of [['Generate AI tags','generate'],['Replace tags manually','correct']]){
        const button=document.createElement('button'); button.textContent=text;
        button.onclick=async()=>{button.disabled=true; try {
          const chosen=ids.value.split(',').map(s=>Number(s.trim()));
          const body={ids:chosen}; if(path==='correct') body.tags=tags.value.split(',').map(s=>s.trim());
          await post('/api/genres/'+path,body);
          status.textContent=path==='generate'?'Queued selected tracks. Use Check status, then refresh Library when complete.':'Shared correction saved. Refresh Library to see labels.';
        } catch(e){status.textContent=e.message;} finally{button.disabled=false;}};
        adminPanel.append(button);
      }
      const check=document.createElement('button'); check.textContent='Check tagging status'; check.onclick=async()=>{try{const r=await fetch('/api/genres'); const d=await r.json(); if(!r.ok) throw Error(d.error); status.textContent=JSON.stringify(d.status);}catch(e){status.textContent=e.message;}}; adminPanel.append(check);
    } catch(e) {status.textContent=e.message;}
  }
  // Initialize after the initial state request without adding polling.
  window.addEventListener('load',()=>setTimeout(init,1000),{once:true});
  return {matches};
})();
