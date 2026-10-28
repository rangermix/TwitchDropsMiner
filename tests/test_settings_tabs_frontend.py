"""Check the Settings sub-tabs cover every section and switch exclusively."""

import json
import re
import subprocess
from pathlib import Path

import pytest

from tests.javascript_helpers import APP_JS, NODE, extract_javascript_function
from tests.test_telegram_frontend import UI_SETUP, run_javascript


INDEX_HTML = Path(__file__).resolve().parents[1] / "web" / "index.html"


def _settings_markup() -> str:
    html = INDEX_HTML.read_text(encoding="utf-8")
    return html[html.index('<div id="settings-tab"') : html.index("<!-- Help Tab -->")]


def test_every_settings_section_sits_in_exactly_one_panel_with_a_tab():
    markup = _settings_markup()
    tabs = re.findall(r'data-settings-tab="([a-z]+)"', markup)
    panels = re.findall(r'id="settings-panel-([a-z]+)"', markup)
    assert tabs == panels == ["general", "mining", "games", "notifications", "account"]
    chunks = markup.split('class="settings-panel"')
    assert "settings-section" not in chunks[0], "section outside any panel"
    assert all("settings-section" in chunk for chunk in chunks[1:])
    # Mining benefits used to live inside the Telegram section.
    telegram = re.search(r'<section[^>]*>(?:(?!</section>).)*telegram-bot-token.*?</section>', markup, re.S)
    assert telegram and "mining-benefit-item" not in telegram.group(0)
    groups = dict(zip(panels, chunks[1:], strict=True))
    assert 'id="reward-priority-section"' in groups['mining']
    assert 'id="priority-list-only"' in groups['games']
    assert 'id="select-linked-btn"' in groups['games']
    assert 'reload-campaigns-btn' in groups['games']
    assert 'id="dashboard-access-slot"' in groups['account']
    assert 'id="inventory-list-view"' not in markup and 'id="reload-btn"' not in markup


@pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend tests")
def test_switch_settings_tab_shows_only_the_selected_panel():
    source = APP_JS.read_text(encoding="utf-8")
    script = extract_javascript_function(source, "switchSettingsTab") + r"""
const assert = require('node:assert/strict');
const names = ['general', 'mining', 'games'];
const buttons = names.map(name => ({
    dataset: { settingsTab: name }, attrs: {}, classes: new Set(),
    classList: { toggle(c, on) { on ? this.owner.classes.add(c) : this.owner.classes.delete(c); } },
    setAttribute(k, v) { this.attrs[k] = v; }
}));
buttons.forEach(b => { b.classList.owner = b; });
const panels = names.map(name => ({ id: `settings-panel-${name}`, hidden: name !== 'general' }));
const document = {
    querySelectorAll: sel => sel === '[data-settings-tab]' ? buttons : panels
};
switchSettingsTab('mining');
assert.deepEqual(panels.map(p => p.hidden), [true, false, true]);
assert.deepEqual(buttons.map(b => b.attrs['aria-selected']), ['false', 'true', 'false']);
assert.deepEqual(buttons.map(b => b.classes.has('active')), [false, true, false]);
"""
    result = subprocess.run([NODE, "-"], input=script, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend tests")
@pytest.mark.parametrize('action', ['hours', 'telegram_save', 'telegram_test', 'discord', 'proxy', 'unlinked', 'reward'])
def test_failed_save_after_leaving_its_panel_remains_visible(action):
    names = ['switchSettingsTab', 'toggleMiningHoursRange', 'onMiningHoursChange',
             'handleSaveTelegramClick', 'testTelegramConnection', 'saveTelegramSettings',
             'saveDiscordSettings', 'updateDiscordSettings', 'saveProxy', 'onUnlinkedMiningChange',
             'saveRewardPriority']
    source = APP_JS.read_text(encoding='utf-8')
    if 'function reportSettingsError(' in source:
        names.append('reportSettingsError')
    script = UI_SETUP + r"""
const panelNames = ['general','mining','games','notifications','account'];
const panels = panelNames.map(name=>({id:'settings-panel-'+name,hidden:name!=='general'}));
const buttons = panelNames.map(name=>({dataset:{settingsTab:name},classList:{toggle(){}},setAttribute(){}}));
document.querySelectorAll=selector=>selector==='[data-settings-tab]'?buttons:panels;
document.activeElement=null;
const panel = name=>panels.find(p=>p.id==='settings-panel-'+name);
const toastCalls=[];
function showToast(message,type){toastCalls.push({message,type});}
function renderRewardPriority(){}
let rewardPrioritySaving=false;
state.settings={telegram_configured:true,discord_configured:true,reward_priority:[],proxy:'',
 mining_hours:{mode:'always',start:'08:00',end:'22:00'}};
Object.assign(state.translations.gui.settings,{mining_hours_save_error:'<b>Hours failed',
 proxy_save_error:'Proxy failed',allow_unlinked_campaigns_save_error:'Unlinked failed',
 reward_priority_error:'Reward failed',discord:{save_error:'Discord failed',error:'Discord test failed'}});
for(const id of ['mining-hours-error','allow-unlinked-error','reward-priority-error'])
 document.getElementById(id).closest=()=>panel('mining');
for(const id of ['telegram-test-result','discord-test-result'])
 document.getElementById(id).closest=()=>panel('notifications');
document.getElementById('proxy-verify-result').closest=()=>panel('general');
document.getElementById('mining-hours-range').checked=true;
for(const id of ['start','end'])Object.assign(document.getElementById('mining-hours-'+id),
 {value:id==='start'?'10:00':'18:00',reportValidity:()=>true});
document.getElementById('telegram-chat-id').value='42';
document.getElementById('proxy-url').value='http://sample-proxy.invalid:8080';
let release;
global.fetch=()=>new Promise(resolve=>{release=()=>resolve({ok:false,status:503,json:async()=>({success:false})});});
const actions={
 hours:{panel:'mining',result:'mining-hours-error',run:onMiningHoursChange},
 telegram_save:{panel:'notifications',result:'telegram-test-result',run:handleSaveTelegramClick},
 telegram_test:{panel:'notifications',result:'telegram-test-result',run:testTelegramConnection},
 discord:{panel:'notifications',result:'discord-test-result',run:()=>updateDiscordSettings('save')},
 proxy:{panel:'general',result:'proxy-verify-result',run:saveProxy},
 unlinked:{panel:'mining',result:'allow-unlinked-error',run:onUnlinkedMiningChange},
 reward:{panel:'mining',result:'reward-priority-error',run:()=>saveRewardPriority(['BADGE','EMOTE','DIRECT_ENTITLEMENT','UNKNOWN'])}
};
""" + f"const action=actions[{action!r}];\n" + r"""
(async()=>{
 switchSettingsTab(action.panel);
 const pending=action.run();
 assert.equal(typeof release,'function','the actual handler reached its asynchronous request');
 switchSettingsTab(action.panel==='general'?'games':'general');
 release();await pending;
 const result=document.getElementById(action.result);
 assert.ok(panel(action.panel).hidden);
 assert.ok(result.textContent);
 assert.deepEqual(toastCalls,[{message:result.textContent,type:'error'}]);
})().catch(error=>{console.error(error);process.exit(1)});
"""
    run_javascript(names, script)


@pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend tests")
def test_tab_click_and_arrow_home_end_keys_keep_one_tab_stop_and_panel():
    run_javascript(['switchSettingsTab', 'handleSettingsTabKeydown', 'initializeSettingsTabs'], r"""
const assert=require('node:assert/strict');
const names=['general','mining','games','notifications','account'];
const buttons=names.map(name=>({dataset:{settingsTab:name},attrs:{},events:{},classList:{toggle(){}},
 setAttribute(k,v){this.attrs[k]=v},addEventListener(k,fn){this.events[k]=fn},focus(){document.activeElement=this}}));
const panels=names.map(name=>({id:'settings-panel-'+name,hidden:name!=='general'}));
const document={activeElement:null,querySelectorAll:s=>s==='[data-settings-tab]'?buttons:panels};
initializeSettingsTabs();
const check=name=>{
 assert.deepEqual(panels.filter(p=>!p.hidden).map(p=>p.id),['settings-panel-'+name]);
 assert.deepEqual(buttons.filter(b=>b.attrs.tabindex==='0').map(b=>b.dataset.settingsTab),[name]);
 assert.deepEqual(buttons.filter(b=>b.attrs['aria-selected']==='true').map(b=>b.dataset.settingsTab),[name]);
};
buttons[2].events.click();check('games');
const key=(index,key)=>{let prevented=false;buttons[index].events.keydown({currentTarget:buttons[index],key,
 preventDefault(){prevented=true}});return prevented};
assert.ok(key(2,'ArrowRight'));check('notifications');assert.equal(document.activeElement,buttons[3]);
assert.ok(key(3,'End'));check('account');
assert.ok(key(4,'ArrowRight'));check('general');
assert.ok(key(0,'ArrowLeft'));check('account');
assert.ok(key(4,'Home'));check('general');
assert.equal(key(0,'Tab'),false);check('general');
switchSettingsTab('unknown');check('general');
""")


@pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend tests")
@pytest.mark.parametrize('locale', ['English', '简体中文', 'العربية'])
def test_tab_labels_use_translations_as_text(locale):
    translations = json.loads((INDEX_HTML.parents[1] / 'lang' / f'{locale}.json').read_text(encoding='utf-8'))
    settings = translations['gui']['settings']
    expected = [settings['general']['name'], *(settings[key] for key in
                ['tab_mining', 'tab_games', 'tab_notifications', 'tab_account'])]
    run_javascript(['applyTranslations'], r"""
const assert=require('node:assert/strict');
const names=['general','mining','games','notifications','account'];
const buttons=Object.fromEntries(names.map(name=>['settings-tab-'+name,{textContent:''}]));
for(const button of Object.values(buttons))Object.defineProperty(button,'innerHTML',{set(){throw Error('unsafe')}});
const document={getElementById:id=>id==='settings-tab'?{querySelector:()=>null}:buttons[id]||null,
 querySelector:()=>null,querySelectorAll:()=>[]};
function translateHistory(){}
function renderGamesToWatch(){}
""" + 'applyTranslations(' + json.dumps(translations) + ');\n'
        + 'assert.deepEqual(names.map(name=>buttons["settings-tab-"+name].textContent),'
        + json.dumps(expected) + ');')


@pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend tests")
def test_visible_inline_failure_does_not_duplicate_a_global_notification():
    run_javascript(['reportSettingsError'], r"""
const assert=require('node:assert/strict');const calls=[];
const panel={hidden:false},tab={classList:{contains:()=>true}};
const element={textContent:'',closest:selector=>selector==='.settings-panel'?panel:tab};
function showToast(...args){calls.push(args)}
reportSettingsError(element,'Translated failure');
assert.equal(element.textContent,'Translated failure');assert.deepEqual(calls,[]);
panel.hidden=true;reportSettingsError(element,'Translated failure');
assert.deepEqual(calls,[['Translated failure','error']]);
""")
