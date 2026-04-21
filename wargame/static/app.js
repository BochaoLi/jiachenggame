// ============================================================================
// Poker Battle 1.8 · 左右分栏 UI (v2)
// ============================================================================
const SUIT_SYM={SPADES:"♠",HEARTS:"♥",CLUBS:"♣",DIAMONDS:"♦"};
const SUIT_CLR={SPADES:"black",HEARTS:"red",CLUBS:"black",DIAMONDS:"red"};
const ACTION_LABEL={recruit:"征兵 (墓地→牌库)",train:"训练 (牌库→手牌)",
  reorganize:"重编 (合并相邻作战区)",balance:"制衡 (手牌↔牌库/墓地)",attack:"进攻"};
let V=0, S=null, draftDeploy=null, selCards=new Set();
let localAttackReport=null;  // 攻击结算报告（右侧展示用）
let localReorgState=null;    // 重编状态
let localMode=null;          // "balance_grave" 等特殊模式

// --- API ---
async function api(p,o={}){return(await fetch(p,{headers:{"Content-Type":"application/json"},...o})).json();}
async function fetchState(){S=await api(`/api/state?viewer=${V}`);return S;}
async function postAction(a,p={}){const r=await api("/api/action",{method:"POST",body:JSON.stringify({action:a,params:p,viewer:V})});if(r.state)S=r.state;return r;}
async function postDecision(ans){const r=await api("/api/attack_decision",{method:"POST",body:JSON.stringify({answer:ans,viewer:V})});if(r.state)S=r.state;return r;}

// --- DOM helpers ---
function el(t,a={},...ch){const e=document.createElement(t);for(const[k,v]of Object.entries(a)){if(k==="class")e.className=v;else if(k==="onclick")e.addEventListener("click",v);else if(k==="html")e.innerHTML=v;else if(v!=null)e.setAttribute(k,v);}for(const c of ch){if(c!=null)e.appendChild(typeof c==="string"?document.createTextNode(c):c);}return e;}
function mkCard(c,opts={}){if(!c)return el("div",{class:"card back"});const clr=SUIT_CLR[c.suit],sym=SUIT_SYM[c.suit];
  const sz=opts.mini?"card mini":"card";
  const d=el("div",{class:`${sz} ${clr} ${opts.cls||""}`.trim()},el("div",{class:"rank-top"},el("span",{},c.rank_label),el("span",{class:"suit"},sym)),el("div",{class:"center-suit"},sym),el("div",{class:"rank-bottom"},el("span",{},c.rank_label),el("span",{class:"suit"},sym)));
  d.dataset.cid=c.id;if(opts.onclick){d.classList.add("selectable");d.addEventListener("click",()=>opts.onclick(c,d));}return d;}
function clearPanel(){document.getElementById("panel-content").innerHTML="";document.getElementById("panel-status").textContent="";}

// ============================================================================
// LEFT PANEL: 场地渲染（始终可见）
// ============================================================================
function renderZones(cid,troops,visible,prefix){
  const c=document.getElementById(cid);c.innerHTML="";
  for(let z=0;z<S.num_zones;z++){
    const t=troops[z];const row=el("div",{class:"bf-cards"});
    if(visible&&t.cards)t.cards.forEach(cd=>row.appendChild(mkCard(cd,{mini:true})));
    else for(let i=0;i<t.size;i++)row.appendChild(el("div",{class:"card mini back"}));
    if(t.size===0)row.appendChild(el("div",{class:"hint",html:"(空)"}));
    c.appendChild(el("div",{class:"battlefield"},el("div",{class:"bf-label"},el("span",{},`${prefix}${z}`),el("span",{},`${t.size}/${S.max_troop}`)),row));
  }
}
function renderTopbar(){
  document.getElementById("turn-info").textContent=`回合 ${S.turn} · ${S.players[S.current].name}`;
  const pm={setup:"设置",mulligan:"换牌",initial_deploy:"初始部署",prepare:"准备",action:"行动",deploy:"部署",game_over:"结束"};
  document.getElementById("phase-badge").textContent=pm[S.phase]||S.phase;
  document.getElementById("ap-badge").textContent=S.phase==="action"?`行动力 ${S.action_points}`:"";
  document.getElementById("ap-badge").style.display=S.phase==="action"?"":"none";
  document.getElementById("grave-count").textContent=S.graveyard_count;
}
function renderFoe(){
  const f=S.players[1-V];
  document.getElementById("foe-name").textContent=f.name+(f.is_current?" (行动中)":"");
  document.getElementById("foe-hand").textContent=f.hand_count;
  document.getElementById("foe-deck").textContent=f.deck_count;
  renderZones("foe-front",f.front,false,"作战");
  renderZones("foe-back",f.back,false,"待战");
}
function renderMe(){
  const m=S.players[V];
  document.getElementById("me-name").textContent=m.name+(m.is_current?" (你的回合)":" (等待)");
  document.getElementById("me-deck").textContent=m.deck_count;
  renderZones("me-front",m.front,true,"作战");
  // 待战区 + 草稿
  const bk=document.getElementById("me-back");bk.innerHTML="";
  for(let z=0;z<S.num_zones;z++){
    const t=m.back[z]; const drafts=(draftDeploy&&draftDeploy[z])||[];
    const tot=t.size+drafts.length;
    const row=el("div",{class:"bf-cards"});
    if(t.cards)t.cards.forEach(c=>row.appendChild(mkCard(c,{mini:true})));
    drafts.forEach(c=>row.appendChild(mkCard(c,{mini:true,cls:"placed"})));
    if(tot===0)row.appendChild(el("div",{class:"hint",html:"(空)"}));
    const isTarget=!!draftDeploy;
    const zone=el("div",{class:"battlefield"+(isTarget?" attack-target":"")},
      el("div",{class:"bf-label"},el("span",{},`待战${z}`+(drafts.length?` (+${drafts.length})`:"")),el("span",{},`${tot}/${S.max_troop}`)),row);
    if(isTarget&&m.is_current){zone.addEventListener("click",(function(zz){return function(){placeToZone(zz);};})(z));}
    bk.appendChild(zone);
  }
  // 手牌
  const hd=document.getElementById("me-hand");hd.innerHTML="";
  const vis=effectiveHand(m);
  const clickable=draftDeploy||S.phase==="mulligan"||localMode==="balance_grave";
  const maxSel=S.phase==="mulligan"?3:localMode==="balance_grave"?2:99;
  vis.forEach(c=>{const sel=selCards.has(c.id);
    hd.appendChild(mkCard(c,{cls:sel?"selected":"",onclick:clickable?function(card){
      if(selCards.has(card.id))selCards.delete(card.id);else if(selCards.size<maxSel)selCards.add(card.id);
      renderMe();
    }:undefined}));
  });
  if(!vis.length)hd.appendChild(el("div",{class:"hint"},"(无手牌)"));
}
function effectiveHand(m){
  if(!draftDeploy||!m.hand)return m.hand||[];
  const u=new Map();for(const z of Object.keys(draftDeploy))for(const c of draftDeploy[z])u.set(c.id,(u.get(c.id)||0)+1);
  const o=[];for(const c of m.hand){if(u.get(c.id)){u.set(c.id,u.get(c.id)-1);}else o.push(c);}return o;
}
function renderLeft(){renderTopbar();renderFoe();renderMe();}

// ============================================================================
// RIGHT PANEL: 统一状态渲染
// ============================================================================
function renderRightPanel(){
  const P=document.getElementById("panel-content");
  const T=document.getElementById("panel-title");
  const ST=document.getElementById("panel-status");
  P.innerHTML="";ST.textContent="";
  const m=S.players[V];

  // 游戏结束
  if(S.phase==="game_over"){
    T.textContent="游戏结束";
    P.appendChild(el("div",{class:"verdict-ok",style:"font-size:18px;margin:20px 0;"},`${S.players[S.winner].name} 获胜！`));
    P.appendChild(el("button",{class:"primary",onclick:showSetup},"再来一局"));return;
  }
  // 攻击报告展示（本地暂存）
  if(localAttackReport){
    T.textContent="攻击结算";
    renderAttackReportInPanel(P,localAttackReport);
    P.appendChild(el("button",{class:"primary",style:"margin-top:12px;",onclick:async()=>{localAttackReport=null;if(S.is_ai_turn){await runAiTurn();}else{render();}}},"确认"));
    return;
  }
  // 重编模式
  if(localReorgState){
    T.textContent="重编";
    renderReorgInPanel(P);return;
  }
  // 攻击进行中（有 pending 决策）
  if(S.pending_request){
    T.textContent="攻击结算 · 决策";
    renderDecisionInPanel(P,S.pending_request);return;
  }
  // 不是自己的回合
  if(!m.is_current){
    T.textContent="等待对手";
    P.appendChild(el("div",{class:"info-text"},`等待 ${S.players[S.current].name} 行动...`));
    P.appendChild(el("button",{onclick:refreshSwap},"刷新"));return;
  }

  // --- 按阶段渲染 ---
  if(S.phase==="mulligan"){
    T.textContent="换牌阶段";
    P.appendChild(el("div",{class:"info-text"},"选择至多 3 张手牌与墓地交换。点击左侧手牌选中（黄框），然后确认。"));
    P.appendChild(el("div",{class:"btn-group"},
      el("button",{class:"primary",onclick:doMulligan},"确认换牌"),
      el("button",{onclick:()=>{selCards.clear();renderMe();}},"清除选择"),
    ));
    ST.textContent=`已选 ${selCards.size}/3 张`;
    return;
  }
  if(S.phase==="initial_deploy"||S.phase==="deploy"){
    const label=S.phase==="initial_deploy"?"初始部署":"部署阶段";
    T.textContent=label;
    if(!draftDeploy){
      P.appendChild(el("div",{class:"info-text"},"将手牌放置到待战区。先点手牌选中，再点左侧待战区格子放入。"));
      P.appendChild(el("div",{class:"btn-group"},
        el("button",{class:"primary",onclick:startDeploy},"开始部署"),
        el("button",{onclick:skipDeploy},"跳过（不部署）"),
      ));
    } else {
      let summary="";
      for(let z=0;z<S.num_zones;z++){const d=draftDeploy[z]||[];if(d.length)summary+=`待战${z}: ${d.map(c=>c.suit_cn+c.rank_label).join(" ")}; `;}
      P.appendChild(el("div",{class:"info-text"},"选手牌（黄框）→ 点左侧待战区（蓝框）放入"));
      if(summary)P.appendChild(el("div",{class:"info-text",style:"color:var(--green);"},summary));
      P.appendChild(el("div",{class:"btn-group"},
        el("button",{class:"primary",onclick:submitDeploy},"提交部署"),
        el("button",{onclick:clearDraft},"清空草稿"),
      ));
      ST.textContent=`已选 ${selCards.size} 张手牌`;
    }
    return;
  }
  if(S.phase==="prepare"){
    T.textContent="准备阶段";
    P.appendChild(el("div",{class:"info-text"},"将待战区的部队前移到作战区。"));
    // 显示各待战区状态
    for(let z=0;z<S.num_zones;z++){
      const bs=m.back[z].size;
      if(bs>0)P.appendChild(el("div",{class:"info-text"},`待战${z}: ${bs}张 → 作战${z}`));
    }
    P.appendChild(el("div",{class:"btn-group"},
      el("button",{class:"primary",onclick:()=>act("prepare",{moves:{}})},"全部前移"),
      el("button",{onclick:()=>act("prepare",{moves:{0:0,1:0,2:0}})},"不移动"),
    ));
    return;
  }
  if(S.phase==="action"){
    if(localMode==="balance_grave"){
      T.textContent="制衡 · 手牌↔墓地";
      P.appendChild(el("div",{class:"info-text"},"选择至多 2 张手牌与墓地交换。"));
      P.appendChild(el("div",{class:"btn-group"},
        el("button",{class:"primary",onclick:doBalanceGrave},"确认交换"),
        el("button",{onclick:()=>{localMode=null;selCards.clear();render();}},"取消"),
      ));
      ST.textContent=`已选 ${selCards.size}/2 张`;
      return;
    }
    if(localMode==="attack_pick"){
      T.textContent="进攻 · 选择作战区";
      P.appendChild(el("div",{class:"info-text"},"点击左侧你的作战区（蓝色高亮）发起攻击。"));
      P.appendChild(el("button",{onclick:()=>{localMode=null;render();}},"取消"));
      return;
    }
    T.textContent="行动阶段";
    P.appendChild(el("div",{class:"info-text"},`行动力 ${S.action_points}/2（已用：${S.actions_used.join(", ")||"无"}）`));
    const bg=el("div",{class:"btn-group",style:"flex-direction:column;"});
    S.available_actions.forEach(a=>{bg.appendChild(el("button",{onclick:()=>onAction(a),style:"text-align:left;"},ACTION_LABEL[a]||a));});
    bg.appendChild(el("button",{onclick:()=>act("end_action_phase"),style:"text-align:left;"},"结束行动 → 进入部署"));
    P.appendChild(bg);
    return;
  }
}

function renderLog(){
  const l=document.getElementById("log");l.innerHTML="";
  (S.log||[]).forEach(e=>{l.appendChild(el("div",{class:e.text.startsWith("  ")?"entry indent":"entry"},e.text));});
  l.scrollTop=l.scrollHeight;
}
function render(){
  if(!S||!S.started)return;
  renderLeft();renderRightPanel();renderLog();
}

// ============================================================================
// 决策渲染（右侧面板内联）
// ============================================================================
function renderDecisionInPanel(P,req){
  const pn=S.players[req.by].name;
  const t=req.type;
  P.appendChild(el("div",{class:"section-label"},`${pn} 需要做出决策`));

  if(t==="ace_choice"){
    if(req.card)P.appendChild(el("div",{class:"card-row"},mkCard(req.card)));
    P.appendChild(el("div",{class:"info-text"},"A 可以计为 11（高点数）或 1（低点数，♥♦可抽更多牌）"));
    P.appendChild(el("div",{class:"btn-group"},
      el("button",{class:"primary big",onclick:()=>submitDec(true)},"A = 11"),
      el("button",{class:"big",onclick:()=>submitDec(false)},"A = 1"),
    ));
  } else if(t==="spade_double_choice"){
    if(req.card)P.appendChild(el("div",{class:"card-row"},mkCard(req.card)));
    P.appendChild(el("div",{class:"info-text"},`♠黑桃防御：当前 ${req.current_val} 点，可翻倍为 ${req.doubled_val} 点`));
    P.appendChild(el("div",{class:"btn-group"},
      el("button",{class:"primary big",onclick:()=>submitDec(true)},"翻倍 (×2)"),
      el("button",{class:"big",onclick:()=>submitDec(false)},"不翻倍"),
    ));
  } else if(t==="rescue_choice"){
    P.appendChild(el("div",{class:"info-text"},"♥红桃急救：可从手牌打出 1 张加入本次防御（每轮限一次）"));
    const bg=el("div",{class:"btn-group",style:"flex-direction:column;"});
    (req.hand||[]).forEach((s,i)=>{bg.appendChild(el("button",{onclick:()=>submitDec(i)},s));});
    bg.appendChild(el("button",{class:"primary",onclick:()=>submitDec(null)},"不使用急救"));
    P.appendChild(bg);
  } else if(t==="continue_defense"){
    P.appendChild(el("div",{class:"info-text"},`防御已成功（${req.current_total} ≥ ${req.attack_total}）。`));
    P.appendChild(el("div",{class:"info-text"},`部队中还剩 ${req.remaining_in_troop} 张牌。继续翻可触发更多弃牌效果。`));
    P.appendChild(el("div",{class:"btn-group"},
      el("button",{onclick:()=>submitDec(true)},"继续翻牌"),
      el("button",{class:"primary",onclick:()=>submitDec(false)},"停止防御"),
    ));
  } else if(t==="attack_target_choice"){
    P.appendChild(el("div",{class:"info-text"},"对应作战区无部队。选择攻击目标："));
    const bg=el("div",{class:"btn-group"});
    if(req.options&&req.options.includes("back"))bg.appendChild(el("button",{class:"big",onclick:()=>submitDec("back")},"攻击待战区"));
    if(req.options&&req.options.includes("hq"))bg.appendChild(el("button",{class:"danger big",onclick:()=>submitDec("hq")},"直攻大本营"));
    P.appendChild(bg);
  } else if(t==="hearts_draw"||t==="diamonds_draw"){
    const icon=t==="hearts_draw"?"♥红桃":"♦方片";
    const src=t==="hearts_draw"?"牌库 → 手牌":"墓地 → 牌库";
    P.appendChild(el("div",{class:"info-text"},`${icon} 弃牌效果：可抽至多 ${req.max} 张（${src}）`));
    const bg=el("div",{class:"btn-group"});
    for(let i=0;i<=req.max;i++)bg.appendChild(el("button",{class:i===req.max?"primary big":"big",onclick:()=>submitDec(i)},`${i}`));
    P.appendChild(bg);
  } else {
    P.appendChild(el("div",{class:"info-text"},`决策类型：${t}`));
    P.appendChild(el("button",{class:"primary",onclick:()=>submitDec(null)},"确定"));
  }
}

async function submitDec(ans){
  const r=await postDecision(ans);
  if(!r.ok){flash(r.message);render();return;}
  if(r.pending){render();return;}
  if(r.attack_report){localAttackReport={report:r.attack_report,game_over:r.game_over||null};}
  // 如果攻击结算完毕但仍然是 AI 回合（AI 攻击完成后还需部署+结束回合）
  if(S.is_ai_turn && !r.pending){
    render(); // 先渲染攻击报告
    // 等用户看完报告后再继续 AI（通过攻击报告的"确认"按钮触发）
    return;
  }
  render();
}

// ============================================================================
// 攻击结算报告（右侧面板内联）
// ============================================================================
function renderAttackReportInPanel(P,data){
  const rpt=data.report; const go=data.game_over;
  P.appendChild(el("div",{class:"section-label"},`目标：${rpt.target_type==="hq"?"大本营":rpt.target_type+"区"+rpt.attacker_zone}`));

  // 攻击牌
  const ab=el("div",{class:"result-block"});
  ab.appendChild(el("div",{class:"section-label"},`攻击牌${rpt.clubs_doubled?" [♣梅花翻倍]":""}`));
  rpt.attack_cards.forEach(e=>{ab.appendChild(el("div",{class:"result-row"},mkCard(e.card,{mini:true}),el("span",{},`= ${e.value}`)));});
  ab.appendChild(el("div",{class:"result-total"},`总攻击 = ${rpt.total_attack}`));
  P.appendChild(ab);

  // 防御牌
  const db=el("div",{class:"result-block"});
  db.appendChild(el("div",{class:"section-label"},"防御牌"));
  if(!rpt.defense_cards||!rpt.defense_cards.length)db.appendChild(el("div",{class:"hint"},"（无防御牌）"));
  (rpt.defense_cards||[]).forEach(e=>{
    const tags=[];
    if(e.source&&e.source!=="front")tags.push(e.source);
    if(e.spade_doubled)tags.push("♠×2");
    db.appendChild(el("div",{class:"result-row"},mkCard(e.card,{mini:true}),el("span",{},`= ${e.value}${tags.length?" ("+tags.join(", ")+")":""}`)));
  });
  db.appendChild(el("div",{class:"result-total"},`累计防御 = ${rpt.total_defense}`));
  if(rpt.silenced)db.appendChild(el("div",{class:"verdict-ok"},"♠ 沉默！攻击方弃牌效果无效"));
  if(rpt.troop_destroyed)db.appendChild(el("div",{class:"verdict-bad"},`部队击毁！溢出 ${rpt.overflow}`));
  else if(rpt.defense_held)db.appendChild(el("div",{class:"verdict-ok"},"防御成功"));
  if(rpt.rescue_used)db.appendChild(el("div",{class:"hint"},"♥ 急救已使用"));
  if(rpt.four_element)db.appendChild(el("div",{class:"verdict-ok"},"四象防御触发！"));
  P.appendChild(db);

  // 溢出报告
  if(rpt.overflow_report){
    P.appendChild(el("div",{class:"section-label"},`── 溢出攻击 → ${rpt.overflow_report.target_type} ──`));
    const ob=el("div",{class:"result-block"});
    (rpt.overflow_report.defense_cards||[]).forEach(e=>{ob.appendChild(el("div",{class:"result-row"},mkCard(e.card,{mini:true}),el("span",{},`= ${e.value}`)));});
    ob.appendChild(el("div",{class:"result-total"},`累计防御 = ${rpt.overflow_report.total_defense}`));
    if(rpt.overflow_report.defense_held)ob.appendChild(el("div",{class:"verdict-ok"},"溢出防御成功"));
    P.appendChild(ob);
  }
  if(go)P.appendChild(el("div",{class:"verdict-bad",style:"font-size:16px;margin-top:10px;"},`游戏结束：${S.players[go.winner].name} 获胜`));
}

// ============================================================================
// 重编（右侧面板内联）
// ============================================================================
function renderReorgInPanel(P){
  const rs=localReorgState;
  const me=S.players[V];
  // 选区域
  const selRow=el("div",{class:"btn-group"});
  selRow.appendChild(el("span",{},"区A:"));
  const selA=el("select");
  for(let z=0;z<S.num_zones;z++)selA.appendChild(el("option",{value:z},`作战${z}`));
  selA.value=String(rs.za);
  selA.onchange=()=>{rs.za=+selA.value;rebuildReorg();};
  selRow.appendChild(selA);
  selRow.appendChild(el("span",{style:"margin-left:8px;"},"区B:"));
  const selB=el("select");
  for(let z=0;z<S.num_zones;z++)selB.appendChild(el("option",{value:z},`作战${z}`));
  selB.value=String(rs.zb);
  selB.onchange=()=>{rs.zb=+selB.value;rebuildReorg();};
  selRow.appendChild(selB);
  P.appendChild(selRow);

  if(Math.abs(rs.za-rs.zb)!==1){
    P.appendChild(el("div",{class:"verdict-bad"},"必须选择相邻的两个作战区"));return;
  }

  // 未分配
  P.appendChild(el("div",{class:"section-label",style:"margin-top:8px;"},"未分配"));
  const poolDiv=el("div",{class:"reorg-pool"});
  rs.pool.forEach(p=>{
    if(rs.assigns[p.key]!=null)return;
    poolDiv.appendChild(el("div",{class:"reorg-row"},
      mkCard(p.card,{mini:true}),
      el("button",{class:"small",onclick:()=>{rs.assigns[p.key]="A";renderRightPanel();}},"→A"),
      el("button",{class:"small",onclick:()=>{rs.assigns[p.key]="B";renderRightPanel();}},"→B"),
    ));
  });
  if(!poolDiv.children.length)poolDiv.appendChild(el("div",{class:"hint"},"(全部已分配)"));
  P.appendChild(poolDiv);

  // 两列
  const targets=el("div",{class:"reorg-targets"});
  for(const side of ["A","B"]){
    const col=el("div");
    col.appendChild(el("div",{class:"section-label"},`区${side}（顶→底）`));
    const box=el("div",{class:"reorg-target"});
    rs.pool.forEach(p=>{
      if(rs.assigns[p.key]!==side)return;
      box.appendChild(el("div",{class:"reorg-row"},
        mkCard(p.card,{mini:true}),
        el("button",{class:"small",onclick:()=>{rs.assigns[p.key]=null;renderRightPanel();}},"回收"),
      ));
    });
    if(!box.children.length)box.appendChild(el("div",{class:"hint"},"(空)"));
    col.appendChild(box);targets.appendChild(col);
  }
  P.appendChild(targets);

  const aCount=rs.pool.filter(p=>rs.assigns[p.key]==="A").length;
  const bCount=rs.pool.filter(p=>rs.assigns[p.key]==="B").length;
  P.appendChild(el("div",{class:"hint"},`A: ${aCount}/${S.max_troop}  B: ${bCount}/${S.max_troop}`));
  P.appendChild(el("div",{class:"btn-group"},
    el("button",{class:"primary",onclick:submitReorg},"提交重编"),
    el("button",{onclick:()=>{localReorgState=null;render();}},"取消"),
  ));
}
function rebuildReorg(){
  const rs=localReorgState;const me=S.players[V];
  rs.pool=[];
  (me.front[rs.za].cards||[]).forEach((c,i)=>rs.pool.push({card:c,key:`A${i}_${c.id}`}));
  (me.front[rs.zb].cards||[]).forEach((c,i)=>rs.pool.push({card:c,key:`B${i}_${c.id}`}));
  rs.assigns={};rs.pool.forEach(p=>rs.assigns[p.key]=null);
  renderRightPanel();
}
async function submitReorg(){
  const rs=localReorgState;
  if(Math.abs(rs.za-rs.zb)!==1){flash("必须相邻");return;}
  const a=rs.pool.filter(p=>rs.assigns[p.key]==="A").map(p=>p.card.id);
  const b=rs.pool.filter(p=>rs.assigns[p.key]==="B").map(p=>p.card.id);
  if(a.length+b.length!==rs.pool.length){flash("还有未分配的牌");return;}
  localReorgState=null;
  await act("reorganize",{zone_a:rs.za,zone_b:rs.zb,new_a:a,new_b:b});
}

// ============================================================================
// 动作通用
// ============================================================================
async function act(a,p={}){
  const r=await postAction(a,p);
  if(!r.ok){flash(r.message);return r;}
  if(r.game_over)flash(`结束：${S.players[r.game_over.winner].name}获胜`);
  if(r.pending){render();return r;}
  if(r.attack_report){localAttackReport={report:r.attack_report,game_over:r.game_over||null};}
  render();return r;
}

async function onAction(a){
  if(a==="attack"){
    localMode="attack_pick";
    render();
    // 高亮作战区
    const fz=document.getElementById("me-front");
    for(let z=0;z<S.num_zones;z++){
      if(S.players[V].front[z].size>0){
        const ze=fz.children[z];ze.classList.add("attack-target");
        ze.addEventListener("click",(function(zz){return async function(){
          localMode=null;
          for(let i=0;i<S.num_zones;i++)fz.children[i].classList.remove("attack-target");
          await act("attack",{zone:zz});
        };})(z));
      }
    }
    return;
  }
  if(a==="balance"){localMode="balance_grave";selCards.clear();render();return;}
  if(a==="reorganize"){
    localReorgState={za:0,zb:1,pool:[],assigns:{}};
    rebuildReorg();return;
  }
  await act(a);
}

// --- 换牌 ---
async function doMulligan(){
  const idx=[];(S.players[V].hand||[]).forEach((c,i)=>{if(selCards.has(c.id))idx.push(i);});
  selCards.clear();await act("mulligan",{indices:idx});
  if(S.is_ai_turn){await runAiTurn();return;}
  if(S.phase==="mulligan"||S.phase==="initial_deploy"){
    if(S.ai_player_idx==null){V=S.current;await fetchState();showSeat();}
    else render();
  }
}

// --- 部署 ---
function startDeploy(){draftDeploy={0:[],1:[],2:[]};selCards.clear();render();}
function clearDraft(){draftDeploy={0:[],1:[],2:[]};selCards.clear();render();}
function placeToZone(z){
  if(!selCards.size){flash("先点手牌选中");return;}
  const m=S.players[V];const vis=effectiveHand(m);
  const chosen=vis.filter(c=>selCards.has(c.id));
  const cur=m.back[z].size+(draftDeploy[z]||[]).length;
  if(cur+chosen.length>S.max_troop){flash(`待战${z}已满`);return;}
  for(const c of chosen)(draftDeploy[z]=draftDeploy[z]||[]).push(c);
  selCards.clear();render();
}
async function submitDeploy(){
  const pl={};for(const z of Object.keys(draftDeploy))if(draftDeploy[z].length)pl[z]=draftDeploy[z].map(c=>c.id);
  const phase=S.phase;
  const r=await act(phase==="initial_deploy"?"initial_deploy":"deploy",{placements:pl});
  draftDeploy=null;selCards.clear();
  if(!r||!r.ok)return;
  if(phase==="initial_deploy"){if(S.is_ai_turn){await runAiTurn();}else if(S.ai_player_idx==null){V=S.current;await fetchState();showSeat();}else render();return;}
  await doEndTurn();
}
async function skipDeploy(){
  const phase=S.phase;
  await act(phase==="initial_deploy"?"initial_deploy":"deploy",{placements:{}});
  draftDeploy=null;selCards.clear();
  if(phase==="initial_deploy"){if(S.is_ai_turn){await runAiTurn();}else if(S.ai_player_idx==null){V=S.current;await fetchState();showSeat();}else render();return;}
  await doEndTurn();
}

// --- 制衡 ---
async function doBalanceGrave(){
  const idx=[];(S.players[V].hand||[]).forEach((c,i)=>{if(selCards.has(c.id))idx.push(i);});
  selCards.clear();localMode=null;await act("balance_graveyard",{hand_indices:idx});
}

// --- 回合 ---
async function doEndTurn(){
  if(S.phase==="game_over"){render();return;}
  await act("end_turn");
  if(S.phase==="game_over"){render();return;}
  if(S.is_ai_turn){await runAiTurn();return;}
  if(S.ai_player_idx==null){V=S.current;await fetchState();showSeat();}
  else render();
}
async function runAiTurn(){
  flash("AI 思考中...");
  await new Promise(r=>setTimeout(r,300));
  let maxIter=20;
  while(S.is_ai_turn && maxIter-->0){
    const r=await api("/api/ai_turn",{method:"POST",body:JSON.stringify({viewer:V})});
    if(r.state)S=r.state;
    if(r.game_over){
      if(r.attack_report)localAttackReport={report:r.attack_report,game_over:r.game_over};
      else flash(`游戏结束：${S.players[r.game_over.winner].name} 获胜`);
      render();return;
    }
    if(r.attack_report)localAttackReport={report:r.attack_report,game_over:null};
    // AI 攻击中人类需要做决策
    if(r.pending && S.pending_request){
      render(); // 渲染决策面板让人类操作
      return;   // 人类操作完后 submitDec 会继续推进
    }
    if(!S.is_ai_turn)break;
    await new Promise(r=>setTimeout(r,150));
  }
  render();
}
async function refreshSwap(){
  await fetchState();
  if(S.phase==="game_over"){render();return;}
  if(S.current!==V){V=S.current;await fetchState();showSeat();}
  else render();
}

// --- 换人 ---
function showSeat(){
  document.getElementById("seat-text").textContent=`请 ${S.players[S.current].name} 入座`;
  document.getElementById("seat-overlay").classList.remove("hidden");
}
document.getElementById("seat-enter").addEventListener("click",()=>{
  document.getElementById("seat-overlay").classList.add("hidden");
  draftDeploy=null;selCards.clear();localMode=null;localAttackReport=null;localReorgState=null;
  render();
});

// --- 入口 ---
function showSetup(){
  document.getElementById("game").classList.add("hidden");
  document.getElementById("setup-overlay").classList.remove("hidden");
  document.getElementById("seat-overlay").classList.add("hidden");
  localAttackReport=null;localReorgState=null;localMode=null;draftDeploy=null;selCards.clear();
}
document.getElementById("s-start").addEventListener("click",async()=>{
  const n1=document.getElementById("s-n1").value.trim()||"玩家A";
  const n2=document.getElementById("s-n2").value.trim()||"玩家B";
  const seed=document.getElementById("s-seed").value.trim();
  const first=+document.getElementById("s-first").value;
  const mode=document.getElementById("s-mode").value;
  const payload={name1:n1,name2:n2,seed,first_player:first,
    four_element:document.getElementById("s-four").checked,
    consecutive:document.getElementById("s-consec").checked};
  if(mode==="ai") payload.ai_player=1;
  const r=await api("/api/new_game",{method:"POST",body:JSON.stringify(payload)});
  if(!r.ok){alert(r.message);return;}
  S=r.state;V=S.ai_player_idx!=null?(1-S.ai_player_idx):S.current;
  draftDeploy=null;selCards.clear();localMode=null;localAttackReport=null;localReorgState=null;
  document.getElementById("setup-overlay").classList.add("hidden");
  document.getElementById("game").classList.remove("hidden");
  if(S.is_ai_turn){await runAiTurn();}
  else if(S.ai_player_idx==null){showSeat();}
  else{render();}
});
document.getElementById("btn-newgame").addEventListener("click",()=>{if(confirm("开始新对局？"))showSetup();});

let _ft=null;
function flash(msg){const s=document.getElementById("panel-status");s.textContent=msg;s.style.color="#ffd166";if(_ft)clearTimeout(_ft);_ft=setTimeout(()=>{s.style.color="";},3000);}
showSetup();
