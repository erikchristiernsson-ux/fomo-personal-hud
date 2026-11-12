"use strict";
const $ = s => document.querySelector(s);
const TZ = "Europe/Stockholm";
const COLORS = {MANAGER:["#ff8a1f","#ff4f1f"],TRADER:["#f59e0b","#ef4444"],SCOUT:["#22d3ee","#6366f1"],SOCIALS:["#ec4899","#a855f7"],FILTER:["#10b981","#0ea5e9"],
  RUGHUNTER:["#ef4444","#b91c1c"],ANALYST:["#8b5cf6","#6366f1"],RISK:["#f43f5e","#a855f7"],BUILDER:["#64748b","#a855f7"]};
let CSRF = null, seen = null, lastState = null, lastChatSig = "", sending = false;

function esc(s){return String(s ?? "").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))}
function money(v){return v==null||isNaN(v)?"missing":(v<0?"-":"")+"$"+Math.abs(v).toFixed(2)}
function tLocal(iso,withDay){ if(!iso) return "missing"; const d=new Date(iso); if(isNaN(d)) return "missing";
  const o={timeZone:TZ,hour:"2-digit",minute:"2-digit",hour12:false}; if(withDay){o.day="numeric";o.month="short"}
  return d.toLocaleString("en-GB",o)}
function ago(s){ if(s==null) return "—"; if(s<60) return "now"; if(s<3600) return Math.floor(s/60)+"m"; if(s<86400) return Math.floor(s/3600)+"h"; return Math.floor(s/86400)+"d"}
function ageOf(iso, fb){ const t=iso?Date.parse(iso):NaN; return isNaN(t)? fb : Math.max(0,Math.round((Date.now()-t)/1000)) }
function sameDay(iso){const f=d=>d.toLocaleDateString("en-GB",{timeZone:TZ}); return f(new Date(iso))===f(new Date())}
function tSmart(iso){return iso? tLocal(iso,!sameDay(iso)) : "missing"}

async function api(path,opt={}){
  const r = await fetch(path,{credentials:"same-origin",...opt,headers:{"Content-Type":"application/json",...(CSRF?{"X-CSRF-Token":CSRF}:{}),...(opt.headers||{})}});
  if(r.status===401){location.href="/login";throw new Error("auth")}
  const j = await r.json().catch(()=>({}));
  if(!r.ok) throw new Error(j.detail||j.error||("HTTP "+r.status));
  return j;
}

/* clock */
function tick(){ $("#clock").textContent = new Date().toLocaleTimeString("en-GB",{timeZone:TZ,hour12:false}) }
setInterval(tick,1000); tick();

/* sparkline */
function spark(svg, vals, w, h, color, fill){
  if(!vals || !vals.length){ svg.innerHTML=""; return }
  let mn=Math.min(...vals), mx=Math.max(...vals); const flat = mx===mn; if(flat){mn-=1;mx+=2}
  const n=vals.length, px=i=> n===1? w/2 : i*(w-4)/(n-1)+2, py=v=> h-3-(v-mn)/(mx-mn)*(h-6);
  const pts = vals.map((v,i)=>px(i).toFixed(1)+","+py(v).toFixed(1)).join(" ");
  const id = "g"+Math.random().toString(36).slice(2,8);
  svg.setAttribute("viewBox",`0 0 ${w} ${h}`);
  svg.innerHTML = `<defs><linearGradient id="${id}" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="${fill}" stop-opacity=".32"/><stop offset="1" stop-color="${fill}" stop-opacity="0"/></linearGradient></defs>`+
    `<polygon points="${px(0)},${h} ${pts} ${px(n-1)},${h}" fill="url(#${id})"/>`+
    `<polyline points="${pts}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
}

/* toasts */
function toast(title, detail){
  const box=$("#toasts"); const el=document.createElement("div"); el.className="toast";
  el.innerHTML=`<span class="kind ev" style="margin-top:5px"></span><span><b>${esc(title)}</b><div>${esc(detail||"")}</div></span>`;
  box.prepend(el); while(box.children.length>3) box.lastChild.remove();
  setTimeout(()=>{el.classList.add("out"); setTimeout(()=>el.remove(),320)},6500);
}

/* render state */
function render(s){
  lastState = s;
  const bal = s.balance.usd;
  $("#heroBal").textContent = money(bal);
  $("#heroSub").textContent = bal==null ? (s.mirror&&s.mirror.mode==="mirror"&&!s.generated ? "Fomo balance missing (no data from the box yet)" : "Fomo balance missing (not found in RULES.md)") : "Your real Fomo balance · from "+s.balance.source;
  const e = s.engine||{}, eq = e.equity;
  const hist = (e.equity_history||[]).map(x=>x.equity);
  spark($("#heroSpark"), hist, 300, 44, "rgba(40,10,0,.7)", "#7a2a00");
  $("#heroSpark").setAttribute("aria-label","paper equity, "+hist.length+" points");
  $("#heroShadow").innerHTML = eq
    ? `<span>Shadow equity (paper)</span><b>${money(eq.equity)} · ${(e.open_positions||[]).length} open · ${esc(tSmart(eq.ts))}</b>`
    : `<span>Shadow ledger (paper, firm.db)</span><b>missing</b>`;
  // goal
  const g = s.goal, m = g.current_multiple;
  $("#goalLabel").textContent = g.label;
  const pct = m==null?0:Math.max(0,Math.min(100,(m-1)/(g.multiple-1)*100));
  $("#goalFill").style.width = pct+"%";
  const flag=$("#goalFlag"); flag.textContent = m==null?"missing":m.toFixed(2)+"×"; flag.style.left = Math.max(4,Math.min(96,pct))+"%";
  $("#goalNow").textContent = "Now "+money(bal);
  $("#goalTarget").textContent = g.target_usd!=null ? "10× = "+money(g.target_usd) : "target missing";
  // header
  const rv = s.rules.rules_version || e.rules_version;
  $("#rulesBadge").textContent = rv ? "Rules v"+rv : "Rules missing";
  const mir = s.mirror||{}, isMirror = mir.mode==="mirror";
  const st = $("#stale"), mb = $("#mirrorBadge");
  if(isMirror){
    mb.hidden=false; mb.textContent = "PHONE MIRROR · "+(mir.age_s==null?"no data yet":(mir.age_s<60?"box data live":"box data "+ago(mir.age_s)+" old"));
    st.hidden = !mir.stale;
    st.textContent = mir.age_s==null ? "Box offline / data stale: no data from the box yet. Values show as missing until it connects."
      : `Box offline / data stale: last update from the box ${ago(mir.age_s)} ago (${tSmart(mir.received)}). Numbers below may be out of date; chat waits until the box is back.`;
  } else { mb.hidden=true; st.hidden=true }
  const dot=$("#engDot"); dot.className="dot "+(isMirror&&mir.stale?"down":(e.status||"down"));
  dot.title = "Engine "+(e.status||"missing")+(e.heartbeat?" · heartbeat "+tLocal(e.heartbeat):"");
  $("#firmName").textContent = s.firm;
  const live = s.bots.filter(b=>b.tag==="live").length;
  $("#firmSub").textContent = `${s.bots.length} bots · ${live} updated in the last 45 min`;
  $("#engBox").innerHTML = e.available ? `Engine <b style="color:${e.status==="ok"?"var(--green)":e.status==="stale"?"var(--amber)":"var(--red)"}">${esc(e.status==="ok"?"running":e.status)}</b> · ${esc(e.mode||"")} · ${esc(e.rules_mode||"")}<br>heartbeat ${esc((()=>{const a=ageOf(e.heartbeat,e.heartbeat_age_s); return a==null?"missing":a<60?"just now":ago(a)+" ago"})())}` : "Engine status missing";
  // held
  const h = s.rules.held;
  $("#held").innerHTML = `<span class="k">HELD</span>` + (h==null ? `<span class="muted">missing</span>` :
    h.length ? h.map(x=>`<span class="coin">${esc(x)}</span>`).join(" ") : `<span>none</span><span class="muted">${esc(s.rules.held_note||"")}</span>`);
  renderBots(s.bots, s.chat);
  renderClosed(s.closed);
  renderFeed(s.feed);
}

function renderBots(bots, chatInfo){
  const prev = {}; document.querySelectorAll(".bot").forEach(el=>prev[el.dataset.k]=el.dataset.u);
  if(!bots.length){ $("#bots").innerHTML = `<div class="empty">Bot status missing: no data from the box yet.</div>`; return }
  $("#bots").innerHTML = bots.map(b=>{
    const [c1,c2] = COLORS[b.key]||["#a855f7","#ec4899"];
    let action = b.action, sub = b.latest_section ? "latest: "+b.latest_section : "";
    if(b.key==="MANAGER"){ const c=(chatInfo&&chatInfo.counts)||{};
      sub = `chat: ${c.queued||0} queued · ${c.delivered||0} waiting for reply · ${c.answered||0} answered`; }
    const eng = b.engine ? `engine ${b.engine.role}: ${b.engine.last_output||"—"}` : "";
    return `<div class="bot" data-k="${b.key}" data-u="${esc(b.updated||"")}">
      <div class="ava" style="background:linear-gradient(135deg,${c1},${c2})">${esc(b.name[0])}</div>
      <div class="bt"><div class="nm"><b>${esc(b.name)}</b><span class="role">${esc(b.role)}</span><span class="tag ${b.tag}">${b.tag==="missing"?"no report":b.tag}</span></div>
        <div class="act ${action?"":"miss"}">${esc(action || ("report missing ("+b.report+" not written yet)"))}</div>
        ${sub?`<div class="sub" title="${esc(sub)}">${esc(sub)}</div>`:""}${eng?`<div class="sub" title="${esc(eng)}">${esc(eng)}</div>`:""}</div>
      <div class="spk"><svg aria-hidden="true"></svg><span>${b.handoffs_total_12h} handoffs · 12h</span></div>
      <div class="big"><b>${b.updated||b.age_s!=null?ago(ageOf(b.updated,b.age_s)):"—"}</b><span>${b.updated?"updated "+esc(tSmart(b.updated)):"no report"}</span></div></div>`}).join("");
  document.querySelectorAll(".bot").forEach((el,i)=>{
    const b=bots[i], [c1]=COLORS[b.key]||["#a855f7"];
    spark(el.querySelector("svg"), b.handoffs_12h, 120, 30, b.handoffs_total_12h?c1:"rgba(255,255,255,.18)", b.handoffs_total_12h?c1:"transparent");
    if(prev[b.key]!==undefined && prev[b.key]!==el.dataset.u){ el.classList.add("flash"); setTimeout(()=>el.classList.remove("flash"),4000) }
  });
}

function renderClosed(c){
  $("#closedCount").textContent = c.available ? c.count : "?";
  if(!c.available){ $("#closedList").innerHTML = `<div class="empty">firm.db missing</div>`; return }
  if(!c.items.length){ $("#closedList").innerHTML = `<div class="empty">No closed shadow trades yet. They appear here when the paper ledger closes one.</div>`; return }
  $("#closedList").innerHTML = c.items.map(p=>{
    const v=p.pnl_usd, cls=v==null?"":v>=0?"pos":"neg";
    return `<div class="row"><span class="t">${esc(tSmart(p.closed))}</span><span class="x"><b>$${esc(p.symbol||"?")}</b><div>${esc(p.exit_reason||"")} · size ${money(p.size_usd)} · v${esc(p.rules_version||"")}</div></span>
      <span class="${cls}">${v==null?"missing":(v>=0?"+":"")+money(v)}${p.pnl_pct!=null?`<div class="sm" style="text-align:right">${(p.pnl_pct>=0?"+":"")+p.pnl_pct.toFixed(1)}%</div>`:""}</span></div>`}).join("");
}

function renderFeed(items){
  if(seen===null){ seen=new Set(items.map(i=>i.id)); }
  else { const fresh = items.filter(i=>!seen.has(i.id)).reverse();
    fresh.slice(-3).forEach(i=>toast(i.title, i.detail)); fresh.forEach(i=>seen.add(i.id)); }
  $("#feed").innerHTML = items.length ? items.map(i=>`<div class="row"><span class="kind ${i.kind==="handoff"?"handoff":i.kind==="report"?"report":"ev"}"></span>
    <span class="x"><b>${esc(i.title)}</b><div title="${esc(i.detail)}">${esc(i.detail)}</div></span><span class="t">${esc(tSmart(i.ts))}</span></div>`).join("")
    : `<div class="empty">No activity yet.</div>`;
}

/* chat */
const ST = {waiting_box:"sent · waiting for the box to pick it up (≤1 min)", relayed:"picked up by the box", queued:"queued · relay picks it up within ~5 min", delivered:"✓ delivered to Mona · waiting for reply", answered:"✓✓ answered"};
function renderChat(c){
  if(c.relay_note) $("#relayNote").textContent = c.relay_note;
  const sig = JSON.stringify(c.messages.map(m=>[m.id,m.status]));
  const cnt=c.counts||{};
  $("#chatCounts").textContent = (cnt.queued||cnt.delivered) ? `${(cnt.queued||0)+(cnt.delivered||0)} waiting` : "";
  if(sig===lastChatSig) return;
  const box=$("#msgs"), atBottom = box.scrollHeight-box.scrollTop-box.clientHeight < 60;
  const prevIds = new Set(lastChatSig ? JSON.parse(lastChatSig).map(x=>x[0]) : []);
  if(lastChatSig) c.messages.filter(m=>m.sender==="mona" && !prevIds.has(m.id)).forEach(m=>toast("Mona replied", m.text));
  lastChatSig = sig;
  box.innerHTML = c.messages.length ? c.messages.map(m=> m.sender==="erik"
    ? `<div class="msg erik">${esc(m.text)}<span class="meta">${esc(tSmart(m.ts))} · <span class="st-${esc(m.status)}">${esc(ST[m.status]||m.status)}</span></span></div>`
    : `<div class="msg mona">${esc(m.text)}<span class="meta">Mona · ${esc(tSmart(m.ts))}</span></div>`).join("")
    : `<div class="empty">No messages yet. Say hi to Mona.</div>`;
  if(atBottom || !prevIds.size) box.scrollTop = box.scrollHeight;
}
async function pollChat(){ try{ renderChat(await api("/api/chat")) }catch(e){} }
async function pollState(){ try{ render(await api("/api/state")) }catch(e){ if(e.message!=="auth") $("#heroSub").textContent="Can't reach the HUD server: "+e.message } }

$("#composer").addEventListener("submit", async ev=>{
  ev.preventDefault(); const ta=$("#chatIn"), text=ta.value.trim(); if(!text||sending) return;
  sending=true; $(".send").disabled=true;
  try{ await api("/api/chat",{method:"POST",body:JSON.stringify({text})}); ta.value=""; ta.style.height=""; await pollChat(); $("#msgs").scrollTop=1e9 }
  catch(e){ toast("Message not sent", e.message) }
  finally{ sending=false; $(".send").disabled=false }
});
$("#chatIn").addEventListener("keydown", e=>{ if(e.key==="Enter" && !e.shiftKey && !e.isComposing){ e.preventDefault(); $("#composer").requestSubmit() } });
$("#chatIn").addEventListener("input", e=>{ e.target.style.height="auto"; e.target.style.height=Math.min(120,e.target.scrollHeight)+"px" });
$("#logout").addEventListener("click", async e=>{ e.preventDefault(); try{ await api("/logout",{method:"POST"}) }catch(_){} location.href="/login" });

(async()=>{
  try{ CSRF = (await api("/api/session")).csrf }catch(e){ return }
  await Promise.all([pollState(), pollChat()]);
  setInterval(pollState, 15000); setInterval(pollChat, 5000);
  document.addEventListener("visibilitychange", ()=>{ if(!document.hidden){ pollState(); pollChat() } });
})();
