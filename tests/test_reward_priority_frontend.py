"""Reward types list: mined checkboxes, drag/keyboard ordering, autosave and failure recovery."""

import json
from pathlib import Path

import pytest

from src.i18n.translator import GUISettings
from tests.javascript_helpers import NODE
from tests.test_priority_fallback_frontend import DOM
from tests.test_telegram_frontend import run_javascript


REWARD_FUNCTIONS = ['makeElement', 'displayedRewardOrder', 'renderRewardPriority', 'saveRewardPriority',
                    'handleDragStart', 'handleDragOver', 'handleDrop']

# A small DOM with ids, classes, focus and parents, enough for the reward types list.
REWARD_DOM = r"""
const assert = require('node:assert/strict');
class Node {
 constructor(tag){this.tag=tag;this.children=[];this.attrs={};this.events={};this.dataset={};this.classes=new Set();
  this._text='';this.disabled=false;this.checked=false;this.hidden=false;this.parentNode=null;this.style={};
  this.classList={toggle:(name,on)=>{on?this.classes.add(name):this.classes.delete(name);},
   add:name=>this.classes.add(name),remove:name=>this.classes.delete(name),contains:name=>this.classes.has(name)};}
 setAttribute(key,value){this.attrs[key]=String(value);if(key==='id')this.id=String(value);
  if(key==='class')String(value).split(' ').forEach(name=>this.classes.add(name));}
 getAttribute(key){return this.attrs[key]??null;}
 set className(value){this.setAttribute('class',value);} get className(){return this.attrs.class||'';}
 set innerHTML(value){assert.equal(value,'');this.replaceChildren();}
 set textContent(value){this._text=String(value);this.children=[];}
 get textContent(){return this._text+this.children.map(child=>child.textContent).join('');}
 appendChild(child){child.parentNode=this;this.children.push(child);return child;}
 replaceChildren(...children){if(walk(this).includes(document.activeElement))document.activeElement=document.body;
  this.children=[];this._text='';children.forEach(child=>this.appendChild(child));}
 insertBefore(child,before){this.children=this.children.filter(item=>item!==child);child.parentNode=this;
  const index=before?this.children.indexOf(before):-1;index<0?this.children.push(child):this.children.splice(index,0,child);}
 get nextSibling(){const siblings=this.parentNode.children;return siblings[siblings.indexOf(this)+1]||null;}
 addEventListener(type,callback){this.events[type]=callback;}
 get value(){return this._value??this.attrs.value??'';} set value(v){this._value=String(v);}
 closest(selector){let node=this;while(node){if(node.classes.has(selector.slice(1)))return node;node=node.parentNode;}return null;}
 querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
 querySelectorAll(selector){const all=this.children.flatMap(child=>[child,...child.querySelectorAll(selector)]);
  return all.filter(node=>selector==='input[type="checkbox"]'?node.tag==='input'&&node.attrs.type==='checkbox':node.classes.has(selector.slice(1)));}
 focus(){if(this.disabled)return;document.activeElement=this;document.dispatchEvent({type:'focusin',target:this});}
}
const root=new Node('root');
for(const id of ['reward-priority-enabled','reward-priority-title','reward-priority-help','reward-priority-enable-label',
 'reward-priority-order','reward-priority-error']) root.appendChild(Object.assign(new Node('div'),{id}));
const walk=node=>[node,...node.children.flatMap(walk)];
const document={body:root,activeElement:root,listeners:new Map(),
 addEventListener(type,callback){if(!this.listeners.has(type))this.listeners.set(type,new Set());this.listeners.get(type).add(callback);},
 removeEventListener(type,callback){this.listeners.get(type)?.delete(callback);},
 dispatchEvent(event){this.listeners.get(event.type)?.forEach(callback=>callback(event));},
 createElement:tag=>new Node(tag),getElementById:id=>walk(root).find(node=>node.id===id)||null};
const rewardTypes=['BADGE','EMOTE','DIRECT_ENTITLEMENT','UNKNOWN'];
const rewardLabelKeys={BADGE:'badge',EMOTE:'emote',DIRECT_ENTITLEMENT:'item',UNKNOWN:'other'};
let rewardPrioritySaving=false;let draggedElement=null;
const state={settings:{reward_priority:[],mining_benefits:{BADGE:true,EMOTE:true,DIRECT_ENTITLEMENT:true,UNKNOWN:false}},
 translations:{gui:{settings:{reward_priority_error:'Translated failure',reward_priority_not_mined:'Not mined',
  reward_priority_rank:'Position of {type}'},inventory:{filters:{badge:'<b>Chat badge</b>',emote:'Emote',item:'Game reward',other:'Other'}}}}};
const calls=[];let response={ok:false};let settingsSaves=0;
async function fetch(url,options){calls.push(JSON.parse(options.body));return response;}
function saveSettings(){settingsSaves++;}
function updateSettingsUI(settings,authoritative,changed){state.settings={...state.settings,...settings};renderRewardPriority();}
const list=()=>document.getElementById('reward-priority-order');
const rows=()=>list().children;
const order=()=>rows().map(row=>row.dataset.rewardType);
const enabled=()=>document.getElementById('reward-priority-enabled');
const ok=saved=>{response={ok:true,json:async()=>({success:true,settings:{reward_priority:saved}})};};
"""


def run_rewards(body: str) -> None:
    run_javascript(REWARD_FUNCTIONS, REWARD_DOM + '(async()=>{\n' + body
                   + '\n})().catch(error=>{console.error(error);process.exit(1)});')


@pytest.mark.skipif(NODE is None, reason='Node required')
def test_rows_merge_mined_checkboxes_with_safe_labels_and_hide_order_when_disabled():
    run_rewards(r"""
const loaded=state.settings.mining_benefits;delete state.settings.mining_benefits;renderRewardPriority();
assert.ok(rows().every(row=>row.querySelector('.reward-type-label').children[0].disabled));
assert.ok(!rows().some(row=>row.textContent.includes('Not mined')));
state.settings.mining_benefits=loaded;renderRewardPriority();
assert.deepEqual(order(),rewardTypes);assert.equal(enabled().checked,false);
assert.equal(list().classes.has('ordered'),false);
assert.ok(rows()[0].textContent.includes('<b>Chat badge</b>'));
const box=type=>rows()[order().indexOf(type)].querySelector('.reward-type-label').children[0];
assert.equal(box('BADGE').checked,true);assert.equal(box('UNKNOWN').checked,false);
assert.ok(rows()[3].classes.has('excluded'));assert.ok(rows()[3].textContent.includes('Not mined'));
assert.equal(rows()[0].draggable,false);assert.equal(document.getElementById('reward-rank-BADGE').disabled,true);
assert.equal(document.getElementById('reward-rank-BADGE').attrs['aria-label'],'Position of <b>Chat badge</b>');
box('BADGE').checked=false;box('BADGE').events.change();
assert.equal(settingsSaves,1);assert.equal(state.settings.mining_benefits.BADGE,false);
assert.ok(rows()[0].textContent.includes('Not mined'));assert.equal(calls.length,0);
""")


@pytest.mark.skipif(NODE is None, reason='Node required')
def test_enabling_order_autosaves_and_failed_save_rolls_back_with_translated_error():
    run_rewards(r"""
renderRewardPriority();
await saveRewardPriority(rewardTypes);
assert.deepEqual(calls,[{reward_priority:rewardTypes}]);
assert.deepEqual(state.settings.reward_priority,[]);assert.equal(enabled().checked,false);
assert.equal(document.getElementById('reward-priority-error').hidden,false);
assert.equal(document.getElementById('reward-priority-error').textContent,'Translated failure');
ok(rewardTypes);await saveRewardPriority(rewardTypes);
assert.deepEqual(state.settings.reward_priority,rewardTypes);assert.equal(enabled().checked,true);
assert.equal(enabled().disabled,false);assert.equal(document.getElementById('reward-priority-error').hidden,true);
assert.ok(list().classes.has('ordered'));assert.equal(rows()[0].draggable,true);
assert.equal(rows()[0].children[1].value,'1');assert.equal(document.getElementById('reward-rank-BADGE').disabled,false);
await saveRewardPriority(rewardTypes);assert.equal(calls.length,2);
""")


@pytest.mark.skipif(NODE is None, reason='Node required')
def test_position_inputs_move_rows_clamp_reject_invalid_and_keep_focus():
    run_rewards(r"""
state.settings.reward_priority=[...rewardTypes];renderRewardPriority();
const rank=type=>document.getElementById('reward-rank-'+type);
for(const bad of ['', '1.5', 'x']){rank('BADGE').value=bad;rank('BADGE').events.change();assert.equal(rank('BADGE').value,'1');}
assert.equal(calls.length,0);
rank('BADGE').focus();const moved=['EMOTE','DIRECT_ENTITLEMENT','BADGE','UNKNOWN'];ok(moved);
rank('BADGE').value='3';rank('BADGE').events.change();await new Promise(resolve=>setTimeout(resolve));
assert.deepEqual(calls.at(-1),{reward_priority:moved});assert.deepEqual(order(),moved);
assert.equal(document.activeElement,rank('BADGE'));assert.equal(rank('BADGE').value,'3');
const last=['EMOTE','DIRECT_ENTITLEMENT','UNKNOWN','BADGE'];ok(last);
rank('BADGE').value='99';rank('BADGE').events.change();await new Promise(resolve=>setTimeout(resolve));
assert.deepEqual(calls.at(-1),{reward_priority:last});
""")


@pytest.mark.skipif(NODE is None, reason='Node required')
@pytest.mark.parametrize('success', [True, False])
@pytest.mark.parametrize('movement', ['unchanged', 'elsewhere', 'internal_checkbox', 'pointer', 'tab'])
def test_delayed_reward_save_respects_focus_navigation(success, movement):
    run_rewards(f'const success={json.dumps(success)}, movement={json.dumps(movement)};\n' + r"""
state.settings.reward_priority=[...rewardTypes];renderRewardPriority();
document.getElementById('reward-rank-BADGE').focus();
const moved=['EMOTE','BADGE','DIRECT_ENTITLEMENT','UNKNOWN'];
let finish;
fetch=()=>new Promise(resolve=>{finish=()=>resolve({ok:success,
 json:async()=>({success:true,settings:{reward_priority:moved}})});});
const saving=saveRewardPriority(moved);
assert.equal(document.activeElement,document.body); // Rendering removed the original input.
const elsewhere=Object.assign(new Node('button'),{id:'different-control'});root.appendChild(elsewhere);
if(movement==='elsewhere')elsewhere.focus();
if(movement==='internal_checkbox'){
 rows()[0].querySelector('.reward-type-label').children[0].focus();
 renderRewardPriority(moved); // Preserve the control chosen by Tab across intervening updates.
}
if(movement==='pointer')document.dispatchEvent({type:'pointerdown',target:document.body});
if(movement==='tab')document.dispatchEvent({type:'keydown',key:'Tab'});
finish();await saving;
assert.deepEqual(state.settings.reward_priority,success?moved:rewardTypes);
assert.equal(document.getElementById('reward-priority-error').hidden,success);
const expected=movement==='unchanged'?document.getElementById('reward-rank-BADGE'):
 movement==='elsewhere'?elsewhere:movement==='internal_checkbox'?
 rows().find(row=>row.dataset.rewardType==='EMOTE').querySelector('.reward-type-label').children[0]:document.body;
assert.ok(document.activeElement===expected,'Saving must respect deliberate focus navigation');
assert.ok([...document.listeners.values()].every(listeners=>listeners.size===0));
""")


@pytest.mark.skipif(NODE is None, reason='Node required')
@pytest.mark.parametrize('control', ['checkbox', 'rank'])
def test_reward_renders_preserve_the_current_control_without_moving_external_focus(control):
    run_rewards(f'const control={json.dumps(control)};\n' + r"""
state.settings.reward_priority=[...rewardTypes];renderRewardPriority();
const find=()=>control==='checkbox'?rows().find(row=>row.dataset.rewardType==='BADGE')
 .querySelector('.reward-type-label').children[0]:document.getElementById('reward-rank-BADGE');
find().focus();
for(let index=0;index<3;index++){
 renderRewardPriority(['EMOTE','BADGE','DIRECT_ENTITLEMENT','UNKNOWN']);
 assert.ok(document.activeElement===find(),'Renders must preserve the currently focused reward control');
}
const elsewhere=Object.assign(new Node('button'),{id:'different-control'});root.appendChild(elsewhere);
elsewhere.focus();renderRewardPriority();
assert.ok(document.activeElement===elsewhere,'Renders must not move focus from outside the list');
""")


@pytest.mark.skipif(NODE is None, reason='Node required')
@pytest.mark.parametrize('success', [True, False])
def test_rank_change_leaves_native_tab_target_until_focus_navigation_completes(success):
    run_rewards(f'const success={json.dumps(success)};\n' + r"""
state.settings.reward_priority=[...rewardTypes];renderRewardPriority();
const rank=document.getElementById('reward-rank-BADGE');rank.focus();rank.value='2';
const checkbox=document.getElementById('reward-mined-BADGE');
const actualSave=saveRewardPriority;let saving,finish;
saveRewardPriority=order=>{saving=actualSave(order);return saving;};
fetch=()=>new Promise(resolve=>{finish=()=>resolve({ok:success,
 json:async()=>({success:true,settings:{reward_priority:['EMOTE','BADGE','DIRECT_ENTITLEMENT','UNKNOWN']}})});});
rank.events.change();
assert.equal(saving,undefined,'A blur-triggered change must finish native focus navigation before saving');
assert.ok(document.getElementById('reward-mined-BADGE')===checkbox,'The pending Tab target must remain connected');
checkbox.focus(); // Browser default Tab navigation follows the change handler.
await new Promise(resolve=>setTimeout(resolve));
assert.ok(saving,'Save starts in the following task');
assert.ok(document.activeElement===document.getElementById('reward-mined-BADGE'));
finish();await saving;
assert.ok(document.activeElement===document.getElementById('reward-mined-BADGE'));
assert.equal(document.getElementById('reward-priority-error').hidden,success);
""")


@pytest.mark.skipif(NODE is None, reason='Node required')
@pytest.mark.parametrize('success', [True, False])
@pytest.mark.parametrize('control', ['priority', 'remove', 'available'])
def test_delayed_reward_save_keeps_focus_in_the_game_list(success, control):
    actual_dom = REWARD_DOM.replace(
        'function updateSettingsUI(settings,authoritative,changed){state.settings={...state.settings,...settings};renderRewardPriority();}',
        '',
    )
    run_javascript(REWARD_FUNCTIONS + ['updateSettingsUI', 'toggleMiningHoursRange',
                                      'renderGamesToWatch', 'renderSelectedGames', 'renderAvailableGames',
                                      'getGameLinkStatus', 'linkStatusSignature', 'makeGameLinkBadge'],
                   actual_dom + f'const success={json.dumps(success)}, control={json.dumps(control)};\n' + r"""
const realGet=document.getElementById.bind(document);
document.getElementById=id=>realGet(id)||(()=>{const element=Object.assign(new Node('div'),{id});root.appendChild(element);return element;})();
let availableGames=new Set(['Game','Other']),selectedInventoryGames=[],renderedLinkSignature=null;
function applyInventoryViewMode(){}function updateGameTagsDisplay(){}function renderChannels(){}function renderInventory(){}
function changeGamePriority(){}function removeGameFromWatch(){}function toggleGameWatch(){}function handleDragEnd(){}
(async()=>{
state.settings={...state.settings,games_to_watch:['Game'],reward_priority:[...rewardTypes]};
state.campaigns={selected:{game_name:'Game',linked:true},available:{game_name:'Other',linked:false}};
renderGamesToWatch();renderRewardPriority();
document.getElementById('reward-rank-BADGE').focus();
const moved=['EMOTE','BADGE','DIRECT_ENTITLEMENT','UNKNOWN'];
let finish;fetch=()=>new Promise(resolve=>{finish=()=>resolve({ok:success,
 json:async()=>({success:true,settings:{...state.settings,reward_priority:moved}})});});
const saving=saveRewardPriority(moved);
const find=()=>control==='available'?document.getElementById('available-games-list').children[0]
 .querySelector('input[type="checkbox"]'):document.getElementById('selected-games-list').children[0]
 .querySelector(control==='priority'?'.priority-input':'.remove-btn');
const original=find();original.focus();finish();await saving;
if(success)assert.ok(find()!==original,'Exercise the actual settings-response game-list render');
assert.ok(document.activeElement===find(),'Reward saves must preserve the game-list control chosen by the user');
assert.deepEqual(state.settings.reward_priority,success?moved:rewardTypes);
assert.ok([...document.listeners.values()].every(listeners=>listeners.size===0));
})().catch(error=>{console.error(error);process.exit(1)});
""")


@pytest.mark.skipif(NODE is None, reason='Node required')
def test_failed_reward_save_does_not_focus_a_now_disabled_rank():
    run_rewards(r"""
state.settings.reward_priority=[...rewardTypes];renderRewardPriority();
document.getElementById('reward-rank-BADGE').focus();
let finish;fetch=()=>new Promise(resolve=>{finish=()=>resolve({ok:false});});
const saving=saveRewardPriority(['EMOTE','BADGE','DIRECT_ENTITLEMENT','UNKNOWN']);
state.settings.reward_priority=[]; // Another dashboard disabled ordering while this request was pending.
finish();await saving;
assert.equal(document.getElementById('reward-rank-BADGE').disabled,true);
assert.equal(document.activeElement,document.body);
assert.ok([...document.listeners.values()].every(listeners=>listeners.size===0));
""")


@pytest.mark.skipif(NODE is None, reason='Node required')
def test_drag_reorders_only_within_its_own_list_and_saves_order():
    run_rewards(r"""
state.settings.reward_priority=[...rewardTypes];renderRewardPriority();
const games=new Node('div');const game=new Node('div');game.setAttribute('class','sortable-item');games.appendChild(game);
const event=target=>({target,preventDefault(){},dataTransfer:{setData(){},effectAllowed:'',dropEffect:''}});
handleDragStart(event(game));handleDragOver(event(rows()[0]));
assert.deepEqual(order(),rewardTypes);assert.equal(game.parentNode,games);
const dragged=rows()[3];handleDragStart(event(dragged));handleDragOver(event(rows()[0]));
const next=['UNKNOWN','BADGE','EMOTE','DIRECT_ENTITLEMENT'];assert.deepEqual(order(),next);
list().classes.delete('ordered');dragged.events.dragend(event(dragged));await Promise.resolve();assert.equal(calls.length,0);
list().classes.add('ordered');ok(next);dragged.events.dragend(event(dragged));await new Promise(resolve=>setTimeout(resolve));
assert.deepEqual(calls.at(-1),{reward_priority:next});assert.deepEqual(state.settings.reward_priority,next);
""")


def test_reward_type_translations_exist_in_all_locales():
    for path in Path('lang').glob('*.json'):
        settings = json.loads(path.read_text(encoding='utf-8'))['gui']['settings']
        for key in GUISettings.__annotations__:
            if key.startswith('reward_priority_'):
                assert settings[key], (path, key)
        assert '{type}' in settings['reward_priority_rank'], path
        assert not {'reward_priority_save', 'mining_benefits', 'mining_benefits_help'} & settings.keys(), path


def test_settings_page_has_one_reward_types_section():
    html = Path('web/index.html').read_text(encoding='utf-8')
    assert 'id="reward-priority-order"' in html
    assert 'settings-benefits-header' not in html and 'reward-priority-save' not in html
    assert 'mining-benefit-' not in html  # checkboxes are rendered per row by renderRewardPriority()
    css = Path('web/static/styles.css').read_text(encoding='utf-8')
    # Must out-rank `.settings-section label { margin-bottom }` so rows stay compact.
    rule = css.split('.reward-type-row .reward-type-label {', 1)[1].split('}', 1)[0]
    assert 'margin: 0' in rule


@pytest.mark.skipif(NODE is None, reason='Node required')
def test_delayed_priority_mode_reply_cannot_reset_completed_reward_policy():
    run_javascript(['updateSettingsUI', 'toggleMiningHoursRange', 'onPriorityModeChange', 'makeElement',
                    'displayedRewardOrder', 'renderRewardPriority', 'saveRewardPriority',
                    'queueSettingsWrite'], DOM + r"""
const elements={};const body=new Element('body');body.classList.remove=()=>{};
const document={body,getElementById:id=>elements[id]??=(()=>{const el=new Element('div');el.classList.toggle=()=>{};return el;})(),
 createElement:tag=>{const el=new Element(tag);el.dataset={};el.classList.toggle=()=>{};return el;}};
const rewardTypes=['BADGE','EMOTE','DIRECT_ENTITLEMENT','UNKNOWN'];
const rewardLabelKeys={BADGE:'badge',EMOTE:'emote',DIRECT_ENTITLEMENT:'item',UNKNOWN:'other'};
let rewardPrioritySaving=false;
function handleDragStart(){}function handleDragOver(){}function handleDrop(){}function saveSettings(){}
const state={settings:{games_to_watch:['Game'],reward_priority:[],mining_benefits:{BADGE:true}},translations:{gui:{settings:{},inventory:{filters:{}}}}};
let availableGames=new Set(),selectedInventoryGames=[];
function applyInventoryViewMode(){}function renderGamesToWatch(){}function renderChannels(){}
function renderInventory(){}function updateGameTagsDisplay(){}function showToast(){}
let release;let backend={...state.settings};
async function fetch(url,options){const request=JSON.parse(options.body);
 if('priority_list_only' in request){backend={...backend,...request};const stale={...backend};
  return await new Promise(resolve=>{release=()=>resolve({ok:true,json:async()=>({success:true,settings:stale})});});}
 backend={...backend,...request};return {ok:true,json:async()=>({success:true,settings:{...backend}})};
}
(async()=>{
updateSettingsUI({...state.settings},true);
document.getElementById('priority-list-only').checked=false;
const delayed=onPriorityModeChange();await Promise.resolve();
state.settings.mining_benefits={BADGE:false};
await queueSettingsWrite({games_to_watch:['Game','Manual'],mining_benefits:state.settings.mining_benefits});
await saveRewardPriority(rewardTypes);
updateSettingsUI({...backend},true); // authoritative socket broadcast
release();await delayed;
assert.deepEqual(backend.reward_priority,rewardTypes);
assert.deepEqual(state.settings.reward_priority,rewardTypes);
assert.deepEqual(state.settings.games_to_watch,['Game','Manual']);
assert.deepEqual(state.settings.mining_benefits,{BADGE:false});
assert.equal(document.getElementById('reward-priority-enabled').checked,true);
assert.deepEqual(document.getElementById('reward-priority-order').children.map(row=>row.dataset.rewardType),rewardTypes);
assert.equal(state.settings.priority_list_only,false);
})().catch(e=>{console.error(e);process.exit(1)});
""")


@pytest.mark.skipif(NODE is None, reason='Node required')
def test_settings_saves_send_loaded_benefits_and_late_replies_keep_local_choices():
    run_javascript(['updateSettingsUI', 'toggleMiningHoursRange', 'parseDropNameBlacklist', 'saveSettings'], DOM + r"""
const elements={};const body=new Element('body');body.classList.remove=()=>{};
const document={body,getElementById:id=>elements[id]??=(()=>{const el=new Element('input');el.value=id==='connection-quality'?'1':id==='minimum-refresh-interval'?'30':'';return el;})()};
const state={settings:{},translations:{gui:{settings:{}}}};
function applyInventoryViewMode(){}function renderGamesToWatch(){}function renderChannels(){}function renderRewardPriority(){}function getInventoryFilters(){return {};}
function renderInventory(){}function updateGameTagsDisplay(){}function showToast(){}
let availableGames=new Set(),selectedInventoryGames=[];const bodies=[];
async function fetch(url,options){bodies.push(JSON.parse(options.body));return {ok:true,json:async()=>({success:true,settings:{mining_benefits:{BADGE:true}}})};}
(async()=>{
await saveSettings();
assert.equal('mining_benefits' in bodies[0],false);
updateSettingsUI({mining_benefits:{BADGE:true,EMOTE:true}},true);
state.settings.mining_benefits={BADGE:false,EMOTE:true};
updateSettingsUI({mining_benefits:{BADGE:true,EMOTE:true}},false,['reward_priority']);
assert.deepEqual(state.settings.mining_benefits,{BADGE:false,EMOTE:true});
await saveSettings();
assert.deepEqual(bodies.at(-1).mining_benefits,{BADGE:false,EMOTE:true});
updateSettingsUI({mining_benefits:{BADGE:true,EMOTE:false}},true);
assert.deepEqual(state.settings.mining_benefits,{BADGE:true,EMOTE:false});
})().catch(e=>{console.error(e);process.exit(1)});
""")
