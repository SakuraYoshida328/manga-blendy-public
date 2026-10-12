const $ = (id) => document.getElementById(id);
const token = document.querySelector('meta[name="manga-token"]').content;
function message(text) { $('message').textContent = text; $('message').hidden = false; }
async function post(url, body={}) {
  const response = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json','X-Manga-Token':token}, body:JSON.stringify(body)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error?.message || JSON.stringify(result));
  return result;
}
function on(id, action) { if($(id)) $(id).onclick = async () => { const b=$(id); b.disabled=true; try { await action(); } catch(e) { message(e.message); } finally { b.disabled=false; } }; }
const progress=$('progress');
if(progress) {
  const {project,job}=progress.dataset, endpoint=`/api/projects/${project}/jobs/${job}`;
  const names={queued:'待機中',starting:'開始中',analyzing:'画像を解析中',planning:'空間を計画中',building:'3Dを準備中',rendering:'背景を撮影中',validating:'出力を検証中',succeeded:'処理が完了しました',failed:'処理に失敗しました',cancelled:'停止しました',interrupted:'処理が中断しています'};
  async function poll() {
    try {
      const response=await fetch(endpoint); if(!response.ok) throw new Error('ジョブを読み込めません。');
      const data=await response.json(); $('job-status').textContent=names[data.status]||data.status;
      if(data.finish_ready && data.status==='succeeded') $('job-status').textContent={awaiting_external_edit:'第1段階完了・外部の漫画仕上げ待ち',awaiting_user_review:'仕上げ候補の確認待ち',approved_output:'承認された漫画仕上げが保存されています'}[data.workflow_status]||'構造画像が保存されています';
      $('elapsed').textContent=`経過 ${data.elapsed_seconds ?? Math.round((Date.now()-Date.parse(data.created_at))/1000)} 秒 / 実行 ${data.attempts} 回`;
      $('job-error').textContent=data.error ? `${data.error.code}: ${data.error.message}` : '';
      const terminal=['succeeded','failed','cancelled','interrupted'].includes(data.status);
      $('cancel-job').hidden=terminal; $('resume-job').hidden=!terminal||data.status==='succeeded'; $('paid-retry').hidden=$('resume-job').hidden;
      $('review-link').hidden=data.status!=='succeeded';
      if(['render','render_image'].includes(data.kind) && data.status==='succeeded') {
        $('review-link').href=data.finish_ready?`/projects/${project}/jobs/${job}/finish`:`/projects/${project}`;
        $('review-link').textContent=data.finish_ready?'二段階仕上げを確認':'資料を開く';
      }
      if(!terminal) setTimeout(poll,2000);
    } catch(e) { message(e.message); }
  }
  on('cancel-job',()=>post(endpoint+'/cancel').then(poll));
  on('resume-job',()=>post(endpoint+'/resume',{allow_paid_retry:$('paid-retry').querySelector('input').checked}).then(poll)); poll();
}
on('setup-diagnose',async()=>{
  const result=await post('/api/setup/diagnose',{operation_mode:'chatgpt_plan',blender_path:$('setup-blender').value});
  $('setup-result').textContent=`Blender: ${result.blender.ready?result.blender.version:result.blender.message}\nMCP: ${result.mcp.message}`;
});
const packet=$('packet');
if(packet) {
  const {project,job,revision,expectedRevision,state,activeState}=packet.dataset;
  const url=`/api/projects/${project}`;
  const goJob=(j)=>location.href=`/projects/${project}/jobs/${j.job_id}`;
  const triple=(text)=> {const v=text.split(',').map(Number); if(v.length!==3||v.some(x=>!Number.isFinite(x))) throw new Error('カンマ区切りの数値を3つ入力してください。'); return v;};
  on('adopt',()=>post(`${url}/jobs/${job}/adopt`,{expected_revision:expectedRevision||null,simplified:$('simplified')?.checked||false}).then(()=>location.href=`/projects/${project}`));
  on('next-state',async()=>{
    const commands=[]; if($('move-object').value) commands.push({object_id:$('move-object').value,op:'Move',value:{translation_m:triple($('move-position').value),quaternion_wxyz:[1,0,0,0],anchor_ref:'world',anchor_key:'origin'},reason:'利用者が配置を指定'});
    const result=await post(url+'/states',{parent_state_id:state,expected_state_id:activeState,story_time:$('story-time').value,commands}); location.href=`/projects/${project}?state_id=${result.state_id}`;
  });
  on('render-state',()=>post(`${url}/states/${state}/render`).then(goJob));
  on('render-manga',()=>post(`${url}/states/${state}/render-image`,{
    stage:'production_drawing',camera_id:$('manga-camera').value,workflow:$('manga-workflow').value,
    render:{focus_mode:$('manga-focus').value,character_outline_px:Number($('manga-outline').value),
      detail_level:$('manga-detail').value||null,resolution:[Number($('manga-width').value),Number($('manga-height').value)]}
  }).then(goJob));
  on('export',()=>post(url+'/export').then(data=>location.href=data.url));
  on('replace-exterior',async()=>{
    if(!$('exterior-image').files.length)throw new Error('屋外用の画像を選択してください。');
    const form=new FormData();form.append('image',$('exterior-image').files[0]);form.append('token',token);
    const response=await fetch(url+'/exterior',{method:'POST',body:form});const result=await response.json();
    if(!response.ok)throw new Error(result.error?.message||'屋外の変更候補を作れません。');goJob(result);
  });
  if($('state-op')) {
    const refresh=()=>{
      const op=$('state-op').value;
      $('container-control').hidden=op!=='stored'; $('open-control').hidden=op!=='open'; $('variant-control').hidden=op!=='variant';
      const variants=JSON.parse($('state-object').selectedOptions[0]?.dataset.variants||'{}');
      $('state-variant').replaceChildren(...Object.entries(variants).map(([id,label])=>new Option(label,id)));
    };
    $('state-op').onchange=refresh; $('state-object').onchange=refresh; refresh();
    on('apply-state',async()=>{
      const op=$('state-op').value; let command={object_id:$('state-object').value,reason:'利用者が場面変更を指定'};
      if(op==='open') { command.op='SetOpenAmount'; command.value=Number($('state-open').value); }
      else if(op==='variant') { command.op='SetVariant'; command.value=$('state-variant').value; }
      else { command.op='SetPresence'; command.value={presence:op}; if(op==='stored') command.value.container_object_id=$('state-container').value; }
      const result=await post(url+'/states',{parent_state_id:state,expected_state_id:activeState,story_time:$('story-time').value,commands:[command]});
      location.href=`/projects/${project}?state_id=${result.state_id}`;
    });
  }
  on('correct',async()=>{
    const correction={object_id:$('correction-object').value};
    if($('correction-dimensions').value) correction.dimensions_m=triple($('correction-dimensions').value);
    if($('correction-position').value) correction.pose={translation_m:triple($('correction-position').value),quaternion_wxyz:[1,0,0,0],anchor_ref:'world',anchor_key:'origin'};
    if($('promote-group').checked) correction.promote_group=true;
    if($('correction-role')?.value) correction.management_role=$('correction-role').value;
    if($('correction-importance')?.value) correction.importance=$('correction-importance').value;
    if($('correction-material')?.value) correction.material_class=$('correction-material').value;
    if($('opening-rows').value || $('opening-columns').value || $('opening-method').value) {
      const opening=JSON.parse($('correction-object').selectedOptions[0].dataset.opening);
      if(!opening) throw new Error('窓かドアを選択してください。');
      if($('opening-rows').value) opening.rows=Number($('opening-rows').value);
      if($('opening-columns').value) opening.columns_per_row=$('opening-columns').value.split(',').map(Number);
      if($('opening-method').value) opening.opening_method=$('opening-method').value==='unknown'?null:$('opening-method').value;
      if($('opening-rows').value || $('opening-columns').value) { opening.row_height_ratios=null; opening.column_width_ratios=null; }
      correction.opening=opening;
    }
    if($('register-variant').value) { correction.variant_id='variant_'+Date.now().toString(36); correction.variant_label=$('register-variant').value; }
    if(Object.keys(correction).length===1) throw new Error('変更内容を入力してください。');
    const endpoint=job ? `${url}/jobs/${job}/correct` : url+'/revise';
    goJob(await post(endpoint,{expected_revision:revision,corrections:[correction]}));
  });
}
const finish=$('finish');
if(finish) {
  const {project,job}=finish.dataset,endpoint=`/api/projects/${project}/jobs/${job}/finish`;
  on('finish-import',async()=>{
    const input=$('finish-image');if(!input.files.length) throw new Error('仕上げ候補のPNGを選択してください。');
    const form=new FormData();form.append('image',input.files[0]);form.append('provider',$('finish-provider').value);
    form.append('model_label',$('finish-model').value||'unknown');form.append('token',token);
    const response=await fetch(endpoint+'/import',{method:'POST',body:form});const result=await response.json();
    if(!response.ok)throw new Error(result.error?.message||'取り込めません。');location.reload();
  });
  document.querySelectorAll('[data-finish-review]').forEach(button=>{
    button.onclick=async()=>{button.disabled=true;try {
      const section=button.closest('section'),structure={};section.querySelectorAll('[data-check]').forEach(e=>structure[e.dataset.check]=e.checked);
      const result=await post(endpoint+'/review',{candidate_id:button.dataset.finishReview,structure,
        appearance_approved:section.querySelector('[data-appearance]').checked,notes:section.querySelector('textarea').value});
      if(result.status==='rejected')message('構造または外観の条件を満たさず不採用です。構造画像は保持しています。');
      else location.reload();
    }catch(e){message(e.message);}finally{button.disabled=false;}};
  });
}
