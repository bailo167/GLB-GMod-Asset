'use strict';
import { GLBViewer } from './viewer.js';

const REQUIRED_SERVICE_VERSION = '2.3.0';
const CENTER_DEPTH = new Set(['head_top','neck_base','shoulder_l','shoulder_r','elbow_l','elbow_r','wrist_l','wrist_r','pelvis','hip_l','hip_r','knee_l','knee_r','ankle_l','ankle_r']);
const REQUIRED = ['head_top','neck_base','shoulder_l','elbow_l','wrist_l','shoulder_r','elbow_r','wrist_r','pelvis','hip_l','knee_l','ankle_l','toe_l','hip_r','knee_r','ankle_r','toe_r'];
const OPTIONAL = ['chin','eye_l','eye_r','hand_tip_l','hand_tip_r','heel_l','heel_r'];
const GROUPS = [
  ['Head and torso', ['head_top','chin','eye_l','eye_r','neck_base','pelvis']],
  ['Left arm', ['shoulder_l','elbow_l','wrist_l','hand_tip_l']],
  ['Right arm', ['shoulder_r','elbow_r','wrist_r','hand_tip_r']],
  ['Left leg', ['hip_l','knee_l','ankle_l','heel_l','toe_l']],
  ['Right leg', ['hip_r','knee_r','ankle_r','heel_r','toe_r']],
];
const PAIRS = { shoulder_l:'shoulder_r', elbow_l:'elbow_r', wrist_l:'wrist_r', hand_tip_l:'hand_tip_r', hip_l:'hip_r', knee_l:'knee_r', ankle_l:'ankle_r', heel_l:'heel_r', toe_l:'toe_r', eye_l:'eye_r' };
Object.entries({...PAIRS}).forEach(([a,b]) => PAIRS[b] = a);

const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const state = { health:null, file:null, inspection:null, project:null, guide:{landmarks:{},rigid_zones:[],locked:false,version:2,notes:''}, selected:null, viewer:null, jobTimer:null, reviewIndex:null };

function escapeHtml(value) { return String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char])); }
function toast(title, detail='') { const node=document.createElement('div'); node.className='toast'; node.innerHTML=`<b>${escapeHtml(title)}</b><span>${escapeHtml(detail)}</span>`; $('#toastStack').append(node); setTimeout(()=>node.remove(),4000); }
function formatBytes(bytes) { const units=['B','KB','MB','GB']; let n=Number(bytes)||0,i=0; while(n>=1024&&i<units.length-1){n/=1024;i++;} return `${n.toFixed(i?1:0)} ${units[i]}`; }
function titleCase(id) { return id.replaceAll('_',' ').replace(/\b\w/g, c=>c.toUpperCase()); }
function slugify(value) { return value.toLowerCase().trim().replace(/[-\s]+/g,'_').replace(/[^a-z0-9_]/g,'_').replace(/_+/g,'_').replace(/^_|_$/g,'').slice(0,48)||'character'; }

async function api(path, options={}) {
  const response = await fetch(path, { cache:'no-store', ...options });
  const type=response.headers.get('content-type')||'';
  const data=type.includes('application/json') ? await response.json() : await response.arrayBuffer();
  if(!response.ok) throw new Error(data?.detail ? `${data.error}: ${data.detail}` : data?.error || `Request failed with ${response.status}`);
  return data;
}

function showPanel(name) {
  $$('.panel-page').forEach(page=>page.classList.toggle('active',page.id===`panel-${name}`));
  $$('.nav-item').forEach(item=>item.classList.toggle('active',item.dataset.panel===name));
  $('#crumbStage').textContent=name.toUpperCase();
  if(name==='projects') loadProjects();
  if(name==='toolchain') loadToolchain();
  if(name==='build'&&state.project){ refreshFiles(); refreshReport(); }
}

function setToolStatus(element, ready, label) { element.textContent=label; element.className=`status ${ready?'ok':'bad'}`; }
async function health() {
  try {
    const data=await api('/api/health'); state.health=data;
    if(data.version!==REQUIRED_SERVICE_VERSION) throw new Error(`Browser UI requires service ${REQUIRED_SERVICE_VERSION}, but ${data.version} answered. Close old Builder windows and restart this package.`);
    $('#serviceDot').className='dot ok'; $('#serviceLabel').textContent=`Service v${data.version}`;
    const tools=data.toolchain.tools;
    setToolStatus($('#blenderStatus'),tools.blender.available,'Blender');
    setToolStatus($('#vtexStatus'),true,'VTF writer');
    setToolStatus($('#compilerStatus'),tools.studiomdl.available&&tools.game_dir.available,'StudioMDL');
  } catch(error) { $('#serviceDot').className='dot bad'; $('#serviceLabel').textContent='Service unavailable'; toast('Local service fault',error.message); }
}

function validateGlbHeader(file) {
  return file.slice(0,12).arrayBuffer().then(buffer=>{
    if(buffer.byteLength<12) throw new Error('The selected file is too small to be a GLB.');
    const view=new DataView(buffer), magic=view.getUint32(0,true), version=view.getUint32(4,true), length=view.getUint32(8,true);
    if(magic!==0x46546c67) throw new Error('The selected file does not have a GLB header.');
    if(version!==2) throw new Error(`Only glTF 2.0 GLB files are supported. This file reports version ${version}.`);
    if(length!==file.size) throw new Error('The GLB header length does not match the file size.');
  });
}

async function selectFile(file) {
  try {
    if(!file) return; if(!file.name.toLowerCase().endsWith('.glb')) throw new Error('Select a file ending in .glb.');
    await validateGlbHeader(file); state.file=file; state.inspection=null;
    $('#importStatus').textContent=`${file.name} · ${formatBytes(file.size)} · header valid`;
    $('#inspectBtn').disabled=false; $('#createProjectBtn').disabled=false;
    if($('#displayName').value==='Guided Character'){ const base=file.name.replace(/\.glb$/i,'').replace(/[_-]+/g,' '); $('#displayName').value=base.replace(/\b\w/g,c=>c.toUpperCase()); $('#slug').value=slugify(base); }
    await inspectSelectedGlb();
  } catch(error) { state.file=null; $('#importStatus').textContent=error.message; $('#inspectBtn').disabled=true; $('#createProjectBtn').disabled=true; toast('GLB rejected',error.message); }
}

async function inspectSelectedGlb() {
  if(!state.file) return;
  $('#inspectionBadge').textContent='INSPECTING'; $('#inspectionBadge').className='badge warn';
  const form=new FormData(); form.append('glb',state.file,state.file.name);
  try {
    const report=await api('/api/inspect',{method:'POST',body:form}); state.inspection=report;
    const values=[report.meshes,report.triangles,report.materials,report.images,report.skins ? 'YES':'NO'];
    $$('#inspectionMetrics b').forEach((node,index)=>node.textContent=Number.isFinite(values[index])?Number(values[index]).toLocaleString():values[index]??'—');
    $('#inspectionBadge').textContent='VALID GLB'; $('#inspectionBadge').className='badge ok';
    const warnings=[]; if(report.triangles>500000)warnings.push('Very dense mesh. The build will reduce it before rigging.'); if(report.skins)warnings.push('An existing skin was detected. V2 replaces it with the locked guided ValveBiped rig.');
    $('#inspectionMessage').textContent=warnings.length?warnings.join(' '):'GLB structure is readable. Create the project and mark the body landmarks.';
  } catch(error) { $('#inspectionBadge').textContent='FAILED'; $('#inspectionBadge').className='badge bad'; $('#inspectionMessage').textContent=error.message; throw error; }
}

function isPropProject() { return state.project?.options?.asset_type==='prop'; }
function projectOptions() {
  return { display_name:$('#displayName').value, slug:slugify($('#slug').value||$('#displayName').value), asset_type:$('#assetType').value, animation_base:$('#animationBase').value, front_axis:$('#frontAxis').value, target_height:Number($('#targetHeight').value), quality:$('#quality').value, texture_size:Number($('#textureSize').value), compile_model:true, package_gma:$('#packageGma').checked, generate_hands:false, generate_npcs:$('#assetType').value==='character'&&$('#generateNpcs').checked };
}
function applyAssetTypeUi() {
  const prop=$('#assetType').value==='prop';
  $$('#panel-import .character-only').forEach(node=>node.classList.toggle('hidden',prop));
  $('#targetHeightLabel').textContent=prop?'Source size, inches, largest dimension':'Source height, inches';
  if(prop&&$('#displayName').value==='Guided Character'){ $('#displayName').value='Scanned Prop'; $('#slug').value=slugify('Scanned Prop'); }
  if(!prop&&$('#displayName').value==='Scanned Prop'){ $('#displayName').value='Guided Character'; $('#slug').value=slugify('Guided Character'); }
}

async function createProject() {
  if(!state.file) return;
  $('#createProjectBtn').disabled=true; $('#createProjectBtn').textContent='Creating Project';
  try {
    const options=projectOptions(); $('#slug').value=options.slug;
    const form=new FormData(); form.append('glb',state.file,state.file.name); form.append('options',JSON.stringify(options));
    const project=await api('/api/projects',{method:'POST',body:form}); await openProject(project.id);
    if(project.options.asset_type==='prop'){ showPanel('build'); toast('Prop project created','No rig guide is needed. Select Build + Install when ready.'); }
    else { showPanel('guide'); toast('Guided project created','Assign the landmarks, review the skeleton and lock the guide.'); }
  } catch(error) { toast('Project creation failed',error.message); }
  finally { $('#createProjectBtn').disabled=false; $('#createProjectBtn').textContent='Create Guided Project'; }
}

async function initViewer() {
  if(state.viewer) return;
  state.viewer=new GLBViewer($('#glCanvas'),$('#markerLayer'),$('#skeletonOverlay'),(id,point)=>assignLandmark(id,point,true,true));
  $$('.viewport-toolbar [data-view]').forEach(button=>button.addEventListener('click',()=>state.viewer.viewPreset(button.dataset.view)));
  $$('.viewport-toolbar [data-mode]').forEach(button=>button.addEventListener('click',()=>{ $$('.viewport-toolbar [data-mode]').forEach(x=>x.classList.remove('active')); button.classList.add('active'); state.viewer.setMode(button.dataset.mode); }));
}

async function openProject(id) {
  try {
    const project=await api(`/api/projects/${id}`); state.project=project; state.guide=project.guide||{landmarks:{},rigid_zones:[],locked:false,version:2}; state.selected=null; state.reviewIndex=null; updateReviewUi();
    $('#activeProjectStatus').textContent=project.options.display_name; $('#activeProjectStatus').className='status ok';
    $('#guideSubtitle').textContent=project.options.asset_type==='prop'
      ?`${project.options.display_name} · static prop · ${project.options.target_height} inches · no rig guide needed`
      :`${project.options.display_name} · ${project.options.target_height} inches · ${project.options.animation_base} animation base`;
    await initViewer(); $('#viewportEmpty').classList.remove('hidden');
    const source=await api(`/api/projects/${id}/source`);
    await state.viewer.load(source,{frontAxis:project.options.front_axis,targetHeight:project.options.target_height});
    state.viewer.setLandmarks(state.guide.landmarks||{}); $('#viewportEmpty').classList.add('hidden');
    renderLandmarks(); renderGuideValidation(project.guide_validation); renderRigidZones(); updateGuideState(); populateBuildSettings();
    refreshFiles(); refreshReport(); showPanel(isPropProject()||project.guide?.locked?'build':'guide');
  } catch(error) {
    const empty=$('#viewportEmpty');
    if(empty){ empty.classList.remove('hidden'); empty.innerHTML=`<b>3D VIEWPORT UNAVAILABLE</b><span>${escapeHtml(error.message)}</span>`; }
    toast('Project could not be opened',error.message);
  }
}

function renderLandmarks() {
  const container=$('#landmarkList'); container.innerHTML='';
  for(const [name,ids] of GROUPS){ const group=document.createElement('div'); group.className='landmark-group'; group.innerHTML=`<h3>${escapeHtml(name)}</h3><div class="landmark-list"></div>`; const list=group.querySelector('.landmark-list');
    for(const id of ids){ const assigned=Boolean(state.guide.landmarks?.[id]), required=REQUIRED.includes(id); const button=document.createElement('button'); button.className=`landmark-button ${required?'required':'optional'} ${assigned?'assigned':''} ${state.selected===id?'active':''}`; button.innerHTML=`<i class="point-dot"></i><span>${escapeHtml(titleCase(id))}</span><small>${required?'REQ':'OPT'}</small>`; button.addEventListener('click',()=>{state.reviewIndex=null;updateReviewUi();selectLandmark(id);}); list.append(button); }
    container.append(group);
  }
  const assigned=REQUIRED.filter(id=>state.guide.landmarks?.[id]).length; $('#landmarkProgress').textContent=`${assigned} / ${REQUIRED.length}`; $('#landmarkProgress').className=`badge ${assigned===REQUIRED.length?'ok':'warn'}`;
  state.viewer?.setLandmarks(state.guide.landmarks||{});
}

function selectLandmark(id) {
  state.selected=id; state.viewer?.setActiveLandmark(id); renderLandmarks(); $('#selectedLandmarkName').textContent=titleCase(id); $('#activeLandmarkLabel').textContent=`Assigning: ${titleCase(id)}`;
  const point=state.guide.landmarks?.[id]; for(const [axis,node] of [['x',$('#coordX')],['y',$('#coordY')],['z',$('#coordZ')]]) node.value=point?Number(point[{x:0,y:1,z:2}[axis]]).toFixed(3):'';
}

function updateReviewUi() {
  const button=$('#reviewPointsBtn'); if(!button)return;
  if(state.reviewIndex===null){button.textContent='Review 17 Points';button.classList.remove('active-review');return;}
  button.textContent=`Review ${state.reviewIndex+1} / ${REQUIRED.length}`;button.classList.add('active-review');
}
function startGuidedReview() {
  if(!state.project)return toast('No active project','Create or open a guided project first.');
  state.reviewIndex=0;updateReviewUi();selectLandmark(REQUIRED[0]);state.viewer?.viewPreset('front');
  toast('Guided placement started','Click each highlighted joint. The tool advances through all 17 required points.');
}
function advanceGuidedReview(currentId) {
  if(state.reviewIndex===null||REQUIRED[state.reviewIndex]!==currentId)return;
  state.reviewIndex+=1;
  if(state.reviewIndex>=REQUIRED.length){state.reviewIndex=null;updateReviewUi();toast('Required point review complete','Inspect the skeleton overlay, then save and validate the guide.');return;}
  updateReviewUi();selectLandmark(REQUIRED[state.reviewIndex]);
}

function assignLandmark(id,point,mirrorMissing=false,fromSurface=false) {
  const numeric=point.map(Number);
  const assigned=fromSurface&&CENTER_DEPTH.has(id)?[0,numeric[1],numeric[2]]:numeric;
  state.guide.locked=false; state.guide.landmarks ||= {}; state.guide.landmarks[id]=assigned;
  if(mirrorMissing&&PAIRS[id]&&!state.guide.landmarks[PAIRS[id]]) state.guide.landmarks[PAIRS[id]]=[assigned[0],-assigned[1],assigned[2]];
  $('#cursorCoordinate').textContent=`X ${assigned[0].toFixed(2)} / Y ${assigned[1].toFixed(2)} / Z ${assigned[2].toFixed(2)}`;
  selectLandmark(id); renderLandmarks(); updateGuideState(); localGuideValidation();
  if(fromSurface) setTimeout(()=>advanceGuidedReview(id),0);
}

function localGuideValidation() {
  const missing=REQUIRED.filter(id=>!state.guide.landmarks?.[id]); const rows=[];
  if(missing.length) rows.push({kind:'bad',text:`Missing ${missing.length} required landmarks: ${missing.map(titleCase).join(', ')}`}); else rows.push({kind:'ok',text:'All required landmarks are assigned.'});
  if(state.guide.landmarks?.shoulder_l&&state.guide.landmarks?.shoulder_r&&state.guide.landmarks.shoulder_l[1]<=state.guide.landmarks.shoulder_r[1]) rows.push({kind:'bad',text:'Left and right shoulders are reversed. Model left must use positive Y.'});
  if(!rows.some(row=>row.kind==='bad')) rows.push({kind:'warn',text:'Save and Validate to run full server checks before locking.'});
  renderValidationRows($('#guideValidation'),rows);
}

function renderValidationRows(container,rows) { container.innerHTML=rows.map(row=>`<div class="check-row ${row.kind==='ok'?'':row.kind}"><i></i><span>${escapeHtml(row.text)}</span></div>`).join(''); }
function renderGuideValidation(validation) {
  if(!validation){localGuideValidation();return;} const rows=[];
  if(validation.errors?.length) validation.errors.forEach(text=>rows.push({kind:'bad',text})); else rows.push({kind:'ok',text:`All ${validation.assigned_required} required landmarks passed geometry checks.`});
  validation.warnings?.forEach(text=>rows.push({kind:'warn',text})); renderValidationRows($('#guideValidation'),rows);
}

function updateGuideState() {
  const prop=isPropProject();
  const locked=Boolean(state.guide.locked); $('#guideStateBadge').textContent=prop?'NOT NEEDED':locked?'LOCKED':'UNLOCKED'; $('#guideStateBadge').className=`badge ${prop||locked?'ok':'warn'}`;
  $('#lockGuideBtn').textContent=locked?'Guide Locked':'Validate and Lock'; $('#runBuildBtn').disabled=!state.project||(!prop&&!locked);
}

async function saveGuide(lock=false) {
  if(!state.project) return toast('No active project','Create or open a project first.');
  if(isPropProject()) return toast('No guide for props','Prop projects skip the rig guide. Use Build + Install directly.');
  const payload={...state.guide,locked:lock};
  try { const project=await api(`/api/projects/${state.project.id}/guide`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}); state.project=project; state.guide=project.guide; state.viewer.setLandmarks(state.guide.landmarks); renderLandmarks(); renderGuideValidation(project.guide_validation); renderRigidZones(); updateGuideState(); toast(lock&&state.guide.locked?'Guide locked':'Guide saved',lock&&!state.guide.locked?'Fix the validation errors before building.':'Landmarks remain in the project workspace.'); if(lock&&state.guide.locked)showPanel('build'); }
  catch(error){toast('Guide save failed',error.message);}
}

function centerSelectedDepth() { if(!state.selected||!state.guide.landmarks?.[state.selected])return toast('No assigned landmark','Select an assigned landmark first.'); const point=state.guide.landmarks[state.selected]; assignLandmark(state.selected,[0,point[1],point[2]],false); toast('Depth centred',`${titleCase(state.selected)} is now on the character centre plane.`); }
function mirrorSelected() { if(!state.selected||!state.guide.landmarks?.[state.selected]||!PAIRS[state.selected])return toast('No mirror pair','Select an assigned left or right landmark.'); const point=state.guide.landmarks[state.selected]; state.guide.landmarks[PAIRS[state.selected]]=[point[0],-point[1],point[2]]; state.guide.locked=false; renderLandmarks(); updateGuideState(); localGuideValidation(); }
function clearGuide() { if(!confirm('Clear every landmark and rigid zone in this project?'))return; state.guide={version:2,locked:false,landmarks:{},rigid_zones:[],notes:''}; state.selected=null; state.reviewIndex=null; updateReviewUi(); state.viewer.setLandmarks({}); renderLandmarks(); renderRigidZones(); updateGuideState(); localGuideValidation(); }
function applyCoordinates() { if(!state.selected)return; const values=[$('#coordX'),$('#coordY'),$('#coordZ')].map(node=>Number(node.value)); if(values.some(v=>!Number.isFinite(v)))return toast('Invalid coordinates','Enter finite X, Y and Z values.'); assignLandmark(state.selected,values,false); }
function removePoint() { if(!state.selected)return; delete state.guide.landmarks[state.selected]; state.guide.locked=false; renderLandmarks(); updateGuideState(); localGuideValidation(); selectLandmark(state.selected); }

function renderRigidZones() {
  const container=$('#rigidZoneList'),zones=state.guide.rigid_zones||[];
  container.innerHTML=zones.length?zones.map(zone=>`<div class="zone-item"><div><b>${escapeHtml(zone.label||'Rigid zone')}</b><small>${escapeHtml(zone.bone)} · R ${Number(zone.radius).toFixed(2)}</small></div><button data-zone-remove="${escapeHtml(zone.id)}">×</button></div>`).join(''):'<div class="notice">No rigid zones.</div>';
  $$('[data-zone-remove]').forEach(button=>button.addEventListener('click',()=>{state.guide.rigid_zones=zones.filter(zone=>zone.id!==button.dataset.zoneRemove);state.guide.locked=false;renderRigidZones();updateGuideState();}));
}
function addRigidZone() { const point=state.selected&&state.guide.landmarks?.[state.selected]; if(!point)return toast('Select an assigned point','A rigid zone needs a centre on the model.'); const bone=$('#rigidBone').value,radius=Number($('#rigidRadius').value); state.guide.rigid_zones||=[]; state.guide.rigid_zones.push({id:crypto.randomUUID().slice(0,8),center:[...point],radius,bone,label:`${titleCase(state.selected)} accessory`}); state.guide.locked=false; renderRigidZones(); updateGuideState(); }

async function runBuild() {
  if(!state.project)return toast('No active project','Create or open a project first.');
  if(!isPropProject()&&!state.guide.locked)return toast('Guide is not locked','Complete and lock the landmark guide first.');
  try { const job=await api(`/api/projects/${state.project.id}/build`,{method:'POST'}); $('#buildLog').textContent='Build queued.\n'; $('#buildState').textContent='QUEUED'; $('#buildProgress').style.width='0%'; clearInterval(state.jobTimer); state.jobTimer=setInterval(()=>pollJob(job.id),800); await pollJob(job.id); }
  catch(error){toast('Build did not start',error.message);}
}
async function pollJob(id) {
  try { const job=await api(`/api/jobs/${id}`); $('#buildState').textContent=job.state.toUpperCase(); $('#buildState').className=`badge ${job.state==='failed'?'bad':job.state==='installed_unverified'?'warn':job.state==='complete'?'ok':''}`; $('#buildProgress').style.width=`${job.progress||0}%`; $('#buildPhase').textContent=job.phase; $('#buildLog').textContent=(job.logs||[]).join('\n')||'Waiting for output.'; $('#buildLog').scrollTop=$('#buildLog').scrollHeight;
    if(!['queued','running'].includes(job.state)){clearInterval(state.jobTimer);state.jobTimer=null;state.project=await api(`/api/projects/${state.project.id}`);refreshFiles();refreshReport();toast(job.state==='installed_unverified'?'Built and installed':'Build ended',job.phase);}
  } catch(error){clearInterval(state.jobTimer);state.jobTimer=null;toast('Build polling failed',error.message);}
}

function flattenChecks(object,prefix='') { const rows=[]; for(const [key,value] of Object.entries(object||{})){const name=prefix?`${prefix}.${key}`:key;if(value&&typeof value==='object'&&!Array.isArray(value))rows.push(...flattenChecks(value,name));else rows.push([name,value]);}return rows; }

// A list check is only a failure when the list means "problems found". Lists that
// enumerate produced artefacts, such as compiled_files, are a failure when EMPTY.
// V2.1.1 marked every non empty array red, so four correctly compiled model files
// were reported as a failed check.
const PROBLEM_LIST_PATTERN=/(^|[._])(errors?|missing|failures?|invalid|unsafe|unweighted|violations?|blocked|conflicts?|warnings?|stale|rejected|overlapping|unpainted)([._]|$)/i;
function checkRowState(name,value){
  if(typeof value==='boolean')return value;
  if(Array.isArray(value))return PROBLEM_LIST_PATTERN.test(name)?value.length===0:value.length>0;
  return true;
}
function populateBuildSettings() {
  const options=state.project?.options; if(!options)return;
  $('#optQuality').value=options.quality;
  $('#optTextureSize').value=String(options.texture_size);
  $('#optTargetHeight').value=String(options.target_height);
  $('#optGenerateNpcs').checked=Boolean(options.generate_npcs);
  $('#optNpcRow').classList.toggle('hidden',options.asset_type==='prop');
  $('#optTargetHeightLabel').textContent=options.asset_type==='prop'?'Source size, inches, largest dimension':'Source height, inches';
}
async function saveBuildSettings() {
  if(!state.project)return;
  const payload={quality:$('#optQuality').value,texture_size:Number($('#optTextureSize').value),target_height:Number($('#optTargetHeight').value),generate_npcs:$('#optGenerateNpcs').checked};
  try {
    const project=await api(`/api/projects/${state.project.id}/options`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    state.project=project; populateBuildSettings();
    $('#guideSubtitle').textContent=project.options.asset_type==='prop'
      ?`${project.options.display_name} · static prop · ${project.options.target_height} inches · no rig guide needed`
      :`${project.options.display_name} · ${project.options.target_height} inches · ${project.options.animation_base} animation base`;
    toast('Build settings saved',`${project.options.quality} quality, ${project.options.texture_size} texture, ${project.options.target_height} inches${project.options.generate_npcs?', NPC variants on':''}. They apply on the next Build + Install.`);
  } catch(error){ toast('Settings not saved',error.message); }
}
async function refreshReport() { if(!state.project)return; try { const report=await api(`/api/projects/${state.project.id}/report`),checks=report.post_build||report.checks||{}; const rows=flattenChecks(checks).filter(([,v])=>typeof v==='boolean'||typeof v==='number'||Array.isArray(v)); $('#buildValidation').innerHTML=rows.length?rows.map(([name,value])=>{const ok=checkRowState(name,value);return`<div class="check-row ${ok?'':'bad'}"><i></i><span><b>${escapeHtml(name.replaceAll('_',' '))}</b><br>${escapeHtml(Array.isArray(value)?JSON.stringify(value):String(value))}</span></div>`}).join(''):'<div class="notice">No strict validation report yet.</div>'; updatePipeline(report.status,checks); refreshTextureProof(checks); }
  catch(error){$('#buildValidation').innerHTML=`<div class="notice">${escapeHtml(error.message)}</div>`;}
}
// The proof renders are written before the texture validation gate runs, so a
// build that FAILS validation still has them on disk. That is precisely when
// they are needed, so the card always tries to load them and only falls back to
// the placeholder when the service says they do not exist.
function refreshTextureProof(checks={}) {
  const host=$('#textureProof'); if(!host)return;
  const badge=$('#textureProofBadge');
  const proven=Boolean(checks.texture_bake_passed);
  const failures=Array.isArray(checks.texture_bake_failures)?checks.texture_bake_failures:[];
  if(badge){
    badge.textContent=proven?'BAKED AND VALIDATED':(failures.length?'REJECTED':'NOT RUN');
    badge.className=`badge ${proven?'ok':(failures.length?'bad':'warn')}`;
  }
  if(!state.project){host.innerHTML='<div class="notice">Open a project to see its baked material proof.</div>';return;}
  host.replaceChildren();
  const placeholder=document.createElement('div');
  placeholder.className='notice';
  placeholder.textContent=failures.length
    ? `The texture bake was rejected: ${failures.join(', ')}. The proof renders below show what was produced before it was rejected.`
    : 'The baked material proof render appears here after a build. It shows the rebuilt UV atlas and the colour baked from the original high resolution GLB.';
  host.append(placeholder);
  const stamp=Date.now();
  let loaded=0;
  for(const view of ['front','back']){
    const figure=document.createElement('figure');
    figure.className='proof';
    const image=document.createElement('img');
    image.alt=`Baked material, ${view} view`;
    image.src=`/api/projects/${state.project.id}/texture-proof?view=${view}&t=${stamp}`;
    image.addEventListener('error',()=>figure.remove());
    image.addEventListener('load',()=>{ if(!loaded++ && !failures.length) placeholder.remove(); });
    const caption=document.createElement('figcaption');
    caption.textContent=view.toUpperCase();
    figure.append(image,caption);
    host.append(figure);
  }
}
function updatePipeline(status,checks={}) { const done={guide:Boolean(checks.guide_locked||state.guide.locked),blender:Boolean(checks.blender_source_created),vtex:Boolean(checks.vtf_written_and_validated||checks.vtex_compiled),studio:Boolean(checks.studiomdl_compiled),runtime:status==='complete'}; $$('#pipelineStages>div').forEach(node=>{node.classList.remove('done','fail');if(done[node.dataset.stage])node.classList.add('done');else if(['texture_blocked','compile_blocked','install_blocked','runtime_failed','failed'].includes(status))node.classList.add('fail');}); }

async function refreshFiles() { if(!state.project)return; try { const data=await api(`/api/projects/${state.project.id}/files`); $('#fileList').innerHTML=data.files?.length?data.files.map(file=>`<div class="file-row"><span>${escapeHtml(file.path)}</span><b>${formatBytes(file.bytes)}</b></div>`).join(''):'<div class="notice">No files.</div>'; }
  catch(error){$('#fileList').innerHTML=`<div class="notice">${escapeHtml(error.message)}</div>`;}
}
async function installProject() { if(!state.project)return; try { const result=await api(`/api/projects/${state.project.id}/install`,{method:'POST'}); toast('Installation repaired',`${result.direct_file_count} files verified. Restart Garry's Mod, enter Sandbox, then read the runtime check.`); state.project=await api(`/api/projects/${state.project.id}`); }
  catch(error){toast('Installation refused',error.message);}
}
async function readRuntimeCheck() { if(!state.project)return; try { const result=await api(`/api/projects/${state.project.id}/runtime-check`); renderRuntime(result); state.project=await api(`/api/projects/${state.project.id}`); refreshReport(); }
  catch(error){toast('Runtime result unavailable',error.message);}
}
function renderRuntime(result) { const passed=result.status==='passed'||result.passed===true; $('#runtimeBadge').textContent=String(result.status||'unknown').toUpperCase(); $('#runtimeBadge').className=`badge ${passed?'ok':result.status==='not_run'?'warn':'bad'}`; const ignored=new Set(['materials','bones','activities','path']); const rows=Object.entries(result).filter(([key,value])=>!ignored.has(key)&&(typeof value==='boolean'||typeof value==='number'||typeof value==='string')).map(([key,value])=>({name:key,value,ok:typeof value==='boolean'?value:key==='material_error_count'?value===0:key==='sequence_count'?value>8:true})); for(const [section,data] of [['Activities',result.activities],['Bones',result.bones]])if(data)for(const [key,value]of Object.entries(data))rows.push({name:`${section}: ${key}`,value:typeof value==='object'?value.valid:value,ok:typeof value==='object'?value.valid:Boolean(value)}); $('#runtimeResult').innerHTML=rows.length?rows.map(row=>`<div class="check-row ${row.ok?'':'bad'}"><i></i><span><b>${escapeHtml(row.name.replaceAll('_',' '))}</b><br>${escapeHtml(typeof row.value==='object'?JSON.stringify(row.value):String(row.value))}</span></div>`).join(''):'<div class="notice">The runtime check has not been written yet. Restart Garry\'s Mod and enter Sandbox.</div>'; }
function downloadOutput() { if(state.project)location.href=`/api/projects/${state.project.id}/download`; }

async function loadProjects() { try { const data=await api('/api/projects'); $('#projectList').innerHTML=data.projects.length?data.projects.map(project=>`<article class="card project-card"><div class="card-head"><h2>${escapeHtml(project.options.display_name)}</h2><span class="badge ${project.state==='complete'?'ok':project.state.includes('failed')?'bad':'warn'}">${escapeHtml(project.state)}</span></div><p>${escapeHtml(project.options.slug)} · ${project.options.asset_type==='prop'?'prop':'character'} · ${new Date(project.updated*1000).toLocaleString()}</p><div class="project-meta"><span class="status">${Number(project.inspection?.triangles||0).toLocaleString()} triangles</span><span class="status">${project.guide_validation?.assigned_required||0}/${project.guide_validation?.required_count||17} landmarks</span></div><div class="actions"><button class="btn primary" data-open-project="${project.id}">Open</button><button class="btn danger" data-delete-project="${project.id}">Delete</button></div></article>`).join(''):'<div class="notice">No projects yet.</div>'; $$('[data-open-project]').forEach(button=>button.addEventListener('click',()=>openProject(button.dataset.openProject))); $$('[data-delete-project]').forEach(button=>button.addEventListener('click',async()=>{if(confirm('Delete this entire project workspace?')){await api(`/api/projects/${button.dataset.deleteProject}`,{method:'DELETE'});loadProjects();}})); }
  catch(error){$('#projectList').innerHTML=`<div class="notice">${escapeHtml(error.message)}</div>`;}
}

async function loadToolchain() { try { const data=await api('/api/config'),cfg=data.config; $('#cfgBlender').value=cfg.blender||'';$('#cfgVtex').value=cfg.vtex||'';$('#cfgStudiomdl').value=cfg.studiomdl||'';$('#cfgGmad').value=cfg.gmad||'';$('#cfgGameDir').value=cfg.game_dir||''; renderToolchain(data.status); }
  catch(error){toast('Toolchain read failed',error.message);}
}
function renderToolchain(status) { const labels={blender:'Blender executable',vtex:'VTEX compatibility tool, optional',studiomdl:'StudioMDL model compiler',gmad:'GMad addon packager',game_dir:"Garry's Mod game directory"}; const rows=Object.entries(status.tools||{}).map(([key,value])=>({kind:(value.available||key==='vtex')?'ok':'bad',text:`${labels[key]}: ${value.available?'Ready':key==='vtex'?'Optional, unused by automatic build':'Missing'}${value.path?` · ${value.path}`:''}`})); rows.splice(1,0,{kind:'ok',text:'Internal VTF 7.2 writer: Ready'}); renderValidationRows($('#toolchainReadiness'),rows); }
async function saveToolchain() { const payload={blender:$('#cfgBlender').value.trim(),vtex:$('#cfgVtex').value.trim(),studiomdl:$('#cfgStudiomdl').value.trim(),gmad:$('#cfgGmad').value.trim(),game_dir:$('#cfgGameDir').value.trim()}; try { const data=await api('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});renderToolchain(data.status);health();toast('Toolchain saved','Paths were checked by the local service.'); }catch(error){toast('Toolchain save failed',error.message);} }

function bind() {
  $$('.nav-item').forEach(button=>button.addEventListener('click',()=>showPanel(button.dataset.panel))); $$('[data-go]').forEach(button=>button.addEventListener('click',()=>showPanel(button.dataset.go)));
  $('#selectGlbBtn').addEventListener('click',()=>{$('#glbInput').value='';$('#glbInput').click();}); $('#glbInput').addEventListener('change',event=>selectFile(event.target.files[0]));
  const drop=$('#dropZone'); ['dragenter','dragover'].forEach(name=>drop.addEventListener(name,event=>{event.preventDefault();drop.classList.add('drag');})); ['dragleave','drop'].forEach(name=>drop.addEventListener(name,event=>{event.preventDefault();drop.classList.remove('drag');})); drop.addEventListener('drop',event=>selectFile(event.dataTransfer.files[0]));
  $('#displayName').addEventListener('input',()=>{$('#slug').value=slugify($('#displayName').value);}); $('#assetType').addEventListener('change',applyAssetTypeUi); $('#inspectBtn').addEventListener('click',inspectSelectedGlb); $('#createProjectBtn').addEventListener('click',createProject);
  $('#reviewPointsBtn').addEventListener('click',startGuidedReview);
  $('#autoSeedBtn').addEventListener('click',()=>{if(!state.viewer||!state.project)return;state.guide.landmarks=state.viewer.autoSeed();state.guide.locked=false;renderLandmarks();updateGuideState();localGuideValidation();toast('Landmarks seeded','Review every marker and click the exact joint positions before locking.');});
  $('#clearGuideBtn').addEventListener('click',clearGuide); $('#saveGuideBtn').addEventListener('click',()=>saveGuide(false)); $('#lockGuideBtn').addEventListener('click',()=>saveGuide(true));
  $('#applyCoordsBtn').addEventListener('click',applyCoordinates); $('#centerDepthBtn').addEventListener('click',centerSelectedDepth); $('#mirrorBtn').addEventListener('click',mirrorSelected); $('#removePointBtn').addEventListener('click',removePoint); $('#addRigidZoneBtn').addEventListener('click',addRigidZone);
  $('#runBuildBtn').addEventListener('click',runBuild); $('#saveOptionsBtn').addEventListener('click',saveBuildSettings); $('#installBtn').addEventListener('click',installProject); $('#runtimeCheckBtn').addEventListener('click',readRuntimeCheck); $('#refreshReportBtn').addEventListener('click',refreshReport); $('#refreshFilesBtn').addEventListener('click',refreshFiles); $('#downloadBtn').addEventListener('click',downloadOutput);
  $('#saveToolchainBtn').addEventListener('click',saveToolchain); $('#refreshToolchainBtn').addEventListener('click',loadToolchain);
}

bind(); applyAssetTypeUi(); renderLandmarks(); localGuideValidation(); updateGuideState(); updateReviewUi(); health(); loadProjects();
