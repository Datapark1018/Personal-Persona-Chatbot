const $ = (selector) => document.querySelector(selector);
const messages = $('#messages');
let profile = null;
let controller = null;
let sessionReady = false;
let lastQuestion = '';
const phaseLabels = {checking_input:'질문을 확인하고 있어요…',retrieving:'관련 자료를 찾고 있어요…',checking_context:'자료를 확인하고 있어요…',generating:'답변을 작성하고 있어요…',validating:'출처와 답변을 확인하고 있어요…',checking_output:'답변을 최종 확인하고 있어요…'};

async function session(signal) {
  const response = await fetch('/api/session', {method:'POST',signal});
  if (!response.ok) throw new Error('대화를 시작하지 못했어요. 잠시 후 다시 시도해 주세요.');
  sessionReady = true;
}
function bubble(role, text='') {
  const box = document.createElement('div'); box.className = `message ${role}`;
  const label=document.createElement('span');label.className='message-label';label.textContent=role==='user'?'나':profile.name;box.append(label);
  const content = document.createElement('span');content.textContent=text;box.append(content);messages.append(box);
  messages.scrollTop = messages.scrollHeight;return {box,content};
}
function busy(value) {
  $('#send').hidden=value;$('#send').disabled=value;$('#stop').hidden=!value;$('#question').disabled=value;$('#reset').disabled=value;
  document.querySelectorAll('[data-question]').forEach(button=>button.disabled=value);
}
async function ask(question) {
  if(!profile || controller || !question.trim()) return;
  lastQuestion=question.trim();$('#question').value='';$('#retry').hidden=true;
  messages.querySelector('.welcome')?.remove();bubble('user',lastQuestion);
  const reply=bubble('assistant-message');
  controller=new AbortController();busy(true);let completed=false;let received='';
  try {
    if(!sessionReady) await session(controller.signal);
    const response=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:lastQuestion}),signal:controller.signal});
    if(!response.ok){if(response.status===401) sessionReady=false;const body=await response.json();throw new Error(body.detail || '요청에 실패했어요.');}
    const reader=response.body.getReader();const decoder=new TextDecoder();let buffer='';
    while(true){
      const {done,value}=await reader.read();buffer+=decoder.decode(value,{stream:!done});
      let end;
      while((end=buffer.indexOf('\n\n'))>=0){
        const frame=buffer.slice(0,end);buffer=buffer.slice(end+2);
        const name=frame.split('\n').find(line=>line.startsWith('event:'))?.slice(6).trim();
        const payload=frame.split('\n').filter(line=>line.startsWith('data:')).map(line=>line.slice(5).trim()).join('\n');
        if(!payload)continue;const data=JSON.parse(payload);
        if(name==='status')$('#status').textContent=phaseLabels[data.phase] || '처리 중…';
        if(name==='delta'){received+=data.text;if(!matchMedia('(prefers-reduced-motion: reduce)').matches)reply.content.textContent=received;}
        if(name==='done'){
          completed=true;reply.content.textContent=received;
          const links=document.createElement('div');links.className='source-list';
          for(const source of data.sources){const a=sourceLink(source.url, '출처: '+source.title);if(a)links.append(a);}
          reply.box.append(links);$('#status').textContent=data.status==='answer'?'답변과 출처를 확인했어요':'공개 자료에 근거해 답변합니다';
          if(data.reason_code==='model_unavailable')$('#retry').hidden=false;
        }
        if(name==='error')throw new Error('답변 전달에 문제가 생겼어요. 다시 시도해 주세요.');
        messages.scrollTop=messages.scrollHeight;
      }
      if(done)break;
    }
    if(!completed)throw new Error('연결이 끊겼어요. 다시 시도해 주세요.');
  }catch(error){
    const note=error.name==='AbortError'?'답변 생성을 중지했어요.':error.message;
    reply.content.textContent=(received ? received+'\n\n' : '')+note;$('#status').textContent=note;$('#retry').hidden=false;
  }finally{controller=null;busy(false);$('#question').focus();}
}
$('#chat-form').addEventListener('submit',e=>{e.preventDefault();ask($('#question').value);});
$('#question').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();ask(e.target.value);}});
document.addEventListener('click',e=>{const button=e.target.closest('[data-question]');if(button){if(innerWidth<761)$('#assistant').scrollIntoView();ask(button.dataset.question);}});
$('#stop').addEventListener('click',()=>controller?.abort());
$('#retry').addEventListener('click',()=>ask(lastQuestion));
$('#open-chat').addEventListener('click',()=>{$('#assistant').scrollIntoView({block:'nearest'});$('#question').focus();});
$('#reset').addEventListener('click',async()=>{
  try{const result=await fetch('/api/session',{method:'DELETE'});if(!result.ok)throw new Error();sessionReady=false;showWelcome();$('#retry').hidden=true;$('#status').textContent='새 대화를 시작합니다';}
  catch{$('#status').textContent='대화를 초기화하지 못했어요. 다시 시도해 주세요.';}
});


function element(tag, className, text) {
  const node=document.createElement(tag);
  if(className)node.className=className;
  if(text)node.textContent=text;
  return node;
}
function icon(name, style='solid') {
  const node=element('i', 'fa-'+style+' fa-'+name);
  node.setAttribute('aria-hidden','true');
  return node;
}
function sourceLink(url, title) {
  if(typeof url !== 'string') return null;
  const internal=/^\/#doc-[a-z][a-z0-9-]{0,63}$/.test(url);
  if(!internal){try{const parsed=new URL(url);if(parsed.protocol!=='https:'||parsed.username||parsed.password)return null;}catch{return null;}}
  const link=element('a');link.href=url;
  link.append(icon(internal?'book-open':'arrow-up-right-from-square'),element('span','',title));
  if(!internal){link.target='_blank';link.rel='noopener noreferrer';}
  return link;
}
function showWelcome() {
  messages.replaceChildren();
  const welcome=element('div','welcome');
  const inPersona = profile.response_mode === 'persona';
  welcome.append(element('h3','',inPersona?'반가워요. '+profile.name+'입니다.':'무엇이 궁금한가요?'),element('p','',inPersona?'제가 만든 것들, 일하는 방식, 관심 있는 기술에 대해 편하게 물어보세요.':profile.name+'의 프로젝트와 경험에 대해 질문해 주세요.'));
  const suggestions=element('div','suggestions');
  for(const question of profile.suggested_questions){
    const button=element('button');button.type='button';button.dataset.question=question;
    const arrow=icon('arrow-right');arrow.classList.add('question-arrow');
    button.append(icon('comment','regular'),element('span','',question),arrow);
    suggestions.append(button);
  }
  welcome.append(suggestions);messages.append(welcome);
}
function renderProfile(data) {
  profile=data;
  document.title=data.name+' · AI Portfolio';
  $('#brand').textContent=data.name;
  const initials=(data.english_name || data.name).trim().split(/\s+/).slice(0,2).map(part=>Array.from(part)[0]).join('').toUpperCase();
  $('#profile-initials').textContent=initials;
  $('#chat-initials').textContent=initials;
  $('#demo-label').textContent=data.fictional?'허구의 인물 · 예제 포트폴리오':'공개 프로필';
  $('#profile-location').textContent=data.location;
  $('#headline').textContent=data.headline;
  $('#bio').textContent=data.bio;
  $('#profile-role').textContent=data.role;
  $('#assistant-name').textContent=data.name;
  $('#assistant-notice').textContent=data.response_mode==='persona'?'AI persona · 공개된 경험과 프로젝트를 바탕으로 대화합니다.':'AI 안내자 · 공개 프로필을 소개합니다.';
  $('#disclosure').textContent=data.fictional?'인물·경력·프로젝트는 허구의 데모입니다. 실제 인물이나 실적을 나타내지 않습니다.':'이 챗봇은 공개 프로필을 설명하는 AI이며 소유자 본인이 아닙니다.';
  $('#footer-name').textContent=data.english_name;
  $('#intro-question').dataset.question=data.name+'을 소개해 주세요.';
  const projects=data.sections.filter(section=>section.category==='project');
  $('#project-count').textContent=projects.length+'개';
  $('#projects').replaceChildren();
  $('#profile-sections').replaceChildren();
  for(const section of data.sections){
    const article=element('article','project');article.id='doc-'+section.id;
    const heading=element('h3');
    if(section.category==='project')heading.append(icon('folder-open','regular'));
    heading.append(element('span','',section.title));
    article.append(heading,element('p','',section.summary));
    const tags=element('div','tags');for(const tag of section.tags.slice(0,6))tags.append(element('span','',tag));article.append(tags);
    const actions=element('div','project-actions');
    const button=element('button');button.append(icon('comment','regular'),element('span','',section.category==='project'?'이 프로젝트 질문하기':'더 물어보기'));button.type='button';button.dataset.question=section.title+'에 대해 설명해 주세요.';button.setAttribute('aria-label',section.title+'에 대해 질문하기');actions.append(button);
    if(section.url){const link=sourceLink(section.url,'관련 페이지');if(link)actions.append(link);}
    article.append(actions);
    $(section.category==='project'?'#projects':'#profile-sections').append(article);
  }
  showWelcome();
}
async function initialize() {
  busy(true);
  try{
    const response=await fetch('/api/persona');if(!response.ok)throw new Error('프로필을 불러오지 못했어요. 페이지를 새로고침해 주세요.');
    renderProfile(await response.json());
    const previous=await fetch('/api/session');
    if(previous.ok){sessionReady=true;const data=await previous.json();if(data.messages.length){messages.replaceChildren();for(const entry of data.messages)bubble(entry.role==='user'?'user':'assistant-message',entry.content);}}
    $('#status').textContent='공개 자료에 근거해 답변합니다';
    busy(false);
  }catch(error){$('#status').textContent=error.message;$('#stop').hidden=true;$('#send').hidden=false;}
}
initialize();
