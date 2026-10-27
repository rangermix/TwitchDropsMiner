"""Priority form validation, translated labels and save failure recovery."""

import json
from pathlib import Path

import pytest

from src.i18n.translator import GUISettings
from tests.javascript_helpers import NODE
from tests.test_priority_fallback_frontend import DOM
from tests.test_telegram_frontend import run_javascript


@pytest.mark.skipif(NODE is None, reason='Node required')
def test_priority_form_rejects_duplicates_and_rolls_back_failed_save():
    run_javascript(['renderRewardPriority', 'saveRewardPriority'], DOM + r"""
const elements={};
const document={getElementById:id=>elements[id]??=(new Element('div')),
 createElement:tag=>{const el=new Element(tag);el.dataset={};el.reportValidity=()=>true;return el;},
 querySelectorAll:()=>document.getElementById('reward-priority-order').children.map(label=>label.children[1])};
const rewardTypes=['BADGE','EMOTE','DIRECT_ENTITLEMENT','UNKNOWN'];
const rewardLabelKeys={BADGE:'badge',EMOTE:'emote',DIRECT_ENTITLEMENT:'item',UNKNOWN:'other'};
let rewardPrioritySaving=false;
const state={settings:{reward_priority:[]},translations:{gui:{settings:{reward_priority_error:'Translated failure'},
 inventory:{filters:{badge:'<b>Chat badge</b>',emote:'Emote',item:'Game reward',other:'Other'}}}}};
let calls=[];let response={ok:false};
async function fetch(url,options){calls.push(JSON.parse(options.body));return response;}
function updateSettingsUI(settings){state.settings=settings;renderRewardPriority();}
(async()=>{
renderRewardPriority();
assert.equal(document.getElementById('reward-priority-order').children[0].children[0].textContent,'<b>Chat badge</b>');
const enabled=document.getElementById('reward-priority-enabled');enabled.checked=true;
let fields=document.querySelectorAll();fields[1].value='1';
await saveRewardPriority();assert.equal(calls.length,0);
assert.equal(document.getElementById('reward-priority-error').hidden,false);
fields[1].value='2';await saveRewardPriority();
assert.equal(calls.length,1);assert.equal(enabled.checked,false);
assert.deepEqual(state.settings.reward_priority,[]);
assert.equal(document.getElementById('reward-priority-error').textContent,'Translated failure');
enabled.checked=true;fields=document.querySelectorAll();fields.forEach((field,i)=>{field.value=String(4-i)});
const saved=[...rewardTypes].reverse();response={ok:true,json:async()=>({success:true,settings:{reward_priority:saved}})};
await saveRewardPriority();assert.deepEqual(state.settings.reward_priority,saved);assert.equal(enabled.disabled,false);
})().catch(e=>{console.error(e);process.exit(1)});
""")


def test_priority_translations_exist_in_all_locales():
    for path in Path('lang').glob('*.json'):
        settings=json.loads(path.read_text())['gui']['settings']
        for key in GUISettings.__annotations__:
            if key.startswith('reward_priority_'):
                assert settings[key], (path, key)


@pytest.mark.skipif(NODE is None, reason='Node required')
def test_delayed_priority_mode_reply_cannot_reset_completed_reward_policy():
    run_javascript(['updateSettingsUI', 'toggleMiningHoursRange', 'onPriorityModeChange',
                    'renderRewardPriority', 'saveRewardPriority'], DOM + r"""
const elements={};const body=new Element('body');body.classList.remove=()=>{};
const document={body,getElementById:id=>elements[id]??=(new Element('div')),
 createElement:tag=>{const el=new Element(tag);el.dataset={};el.reportValidity=()=>true;return el;},
 querySelectorAll:()=>document.getElementById('reward-priority-order').children.map(label=>label.children[1])};
const rewardTypes=['BADGE','EMOTE','DIRECT_ENTITLEMENT','UNKNOWN'];
const rewardLabelKeys={BADGE:'badge',EMOTE:'emote',DIRECT_ENTITLEMENT:'item',UNKNOWN:'other'};
let rewardPrioritySaving=false;
const state={settings:{games_to_watch:['Game'],reward_priority:[]},translations:{gui:{settings:{},inventory:{filters:{}}}}};
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
document.getElementById('reward-priority-enabled').checked=true;
await saveRewardPriority();
updateSettingsUI({...backend},true); // authoritative socket broadcast
release();await delayed;
assert.deepEqual(backend.reward_priority,rewardTypes);
assert.deepEqual(state.settings.reward_priority,rewardTypes);
assert.equal(document.getElementById('reward-priority-enabled').checked,true);
assert.deepEqual(document.querySelectorAll().map(input=>input.dataset.rewardType),rewardTypes);
assert.equal(state.settings.priority_list_only,false);
})().catch(e=>{console.error(e);process.exit(1)});
""")
