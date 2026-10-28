"""Inventory accordion and transactional watch-list shortcuts using an isolated DOM."""

import json
import subprocess
from pathlib import Path

import pytest

from src.i18n.translator import GUIInventory
from tests.javascript_helpers import APP_JS, NODE, extract_javascript_function


ROOT = Path(__file__).resolve().parents[1]

HARNESS = r"""
const assert = require('node:assert/strict');
class Element {
 constructor(tag){this.tag=tag;this.children=[];this.attrs={};this.events={};this.style={};this._text='';this.disabled=false;this.hidden=false;}
 setAttribute(key,value){this.attrs[key]=String(value);if(key==='id')this.id=String(value);if(key==='class')this.className=String(value);}
 getAttribute(key){return this.attrs[key] ?? null;}
 appendChild(child){this.children.push(child);child.parentElement=this;return child;}
 replaceChildren(...children){if(this.contains(document.activeElement))document.activeElement=document.body;this.children=[];this._text='';children.forEach(child=>this.appendChild(child));}
 set textContent(value){this._text=String(value);this.children=[];}
 get textContent(){return this._text+this.children.map(child=>child.textContent).join('');}
 addEventListener(type,callback){this.events[type]=callback;}
 contains(target){return this===target || this.children.some(child=>child.contains(target));}
 focus(){if(!this.disabled)document.activeElement=this;}
 click(){this.events.click?.({target:this});}
 closest(selector){const parts=selector.split(',').map(part=>part.trim());let node=this;
  const matches=part=>part.startsWith('.')?(node.className||'').split(' ').includes(part.slice(1)):node.tag===part;
  while(node){if(parts.some(matches))return node;node=node.parentElement;}return null;}
}
const body=new Element('body');const grid=new Element('div');grid.id='inventory-grid';grid.classList={toggle(name,on){this[name]=on;},contains(name){return this[name]===true;}};grid.dataset={};grid.clientWidth=0;body.appendChild(grid);
function getComputedStyle(){return {getPropertyValue:name=>name==='--inventory-card-min'?'350px':'',columnGap:'20px'};}
for(const id of ['inventory-view-cards','inventory-view-list'])body.appendChild(Object.assign(new Element('button'),{id}));
for(const id of ['dark-mode','language','connection-quality','minimum-refresh-interval','drop-name-blacklist']) {
 const control=new Element('input');control.id=id;control.value=id==='connection-quality'?'1':id==='minimum-refresh-interval'?'30':'';
 body.appendChild(control);
}
function walk(root){return [root,...root.children.flatMap(walk)];}
const document={body,activeElement:body,createElement:tag=>new Element(tag),
 getElementById:id=>walk(body).find(node=>node.id===id) || null,
 querySelectorAll:selector=>walk(body).filter(node=>(node.className||'').split(' ').includes(selector.slice(1)))};
const state={settings:{games_to_watch:['First','Manual']},translations:{gui:{inventory:{
 add_game:'Add to Games to Watch',adding_game:'Adding',add_game_error:'Save failed',
 link_account:'Link account',filters:{linked:'Linked',not_linked:'Not linked',item:'Item'}}}},campaigns:{}};
const expandedInventoryCampaigns=new Set();let inventoryWatchSavePending=false;let inventoryWatchPendingGame=null;
const gameWatchWrites={confirmed:null,draft:null,pending:[],tail:Promise.resolve(),revision:0};
const toasts=[];const requests=[];
let responder=async()=>({ok:true,json:async()=>({success:true,settings:{games_to_watch:['First','Manual','Cyberpunk 2077']}})});
async function fetch(url,options){requests.push({url,options});return responder();}
function showToast(...args){toasts.push(args);}
let selectionText='';function getSelection(){return {toString:()=>selectionText};}
function updateDropArt(){}
function getInventoryFilters(){return {};}
function campaignMatchesFilters(){return true;}
function renderGamesToWatch(){} function renderChannels(){}
function updateSettingsUI(settings){state.settings=reconcileGameWatchSettings(settings);renderInventory();}
function makeImageElement(src,alt,className){return makeElement('img',{src,alt,class:className});}
function campaign(id='one',changes={}){return {id,name:'Quadra Paintjobs',game_name:'Cyberpunk 2077',
 game_box_art_url:'https://example.test/{width}x{height}.jpg',linked:true,active:true,
 campaign_url:'https://www.twitch.tv/drops/campaigns',link_url:'https://example.test/link',
 starts_at:'2026-10-01T00:00:00Z',ends_at:'2026-11-19T09:59:59Z',claimed_drops:0,total_drops:1,
 drops:[{name:'Quadra Paintjobs',current_minutes:10,required_minutes:120,progress:1/12,benefits:[{name:'Short-Fuse Neon',image_url:'https://example.test/car.jpg',type:'DIRECT_ENTITLEMENT'}]}],...changes};}
const buttons=()=>document.querySelectorAll('.inventory-add-game');
const toggle=id=>document.getElementById('inventory-toggle-'+id);
const content=id=>document.getElementById('inventory-rewards-'+id);
"""


def run_inventory(body):
    source = APP_JS.read_text(encoding='utf-8')
    functions = '\n'.join(extract_javascript_function(source, name) for name in (
        'makeElement', 'canAddInventoryGame', 'renderInventory', 'applyGameWatchEdit',
        'reconcileGameWatchSettings', 'queueSettingsWrite', 'parseDropNameBlacklist',
        'applyInventoryViewMode', 'setInventoryViewMode', 'inventoryColumnCount'))
    functions += '\nasync ' + extract_javascript_function(source, 'addInventoryGame')
    functions += '\nasync ' + extract_javascript_function(source, 'saveSettings')
    result = subprocess.run([NODE, '-'], input=HARNESS + '\n' + functions + '\n(async()=>{\nreconcileGameWatchSettings(state.settings);\n' + body
        + '\n})().catch(error=>{console.error(error);process.exit(1)});', text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


pytestmark = pytest.mark.skipif(NODE is None, reason='Node.js required for frontend interaction tests')


def test_shortcut_only_for_linked_unselected_games_and_uses_safe_text():
    run_inventory(r"""
state.campaigns={one:campaign(),two:campaign('two',{linked:false}),three:campaign('three',{game_name:'FIRST'}),
 four:campaign('four',{game_name:'<img onerror=bad()>'}),five:campaign('five',{game_name:''})};
renderInventory();assert.equal(buttons().length,2);
assert.equal(document.querySelectorAll('.campaign-toggle').length,5);
assert.ok(grid.textContent.includes('<img onerror=bad()>'));
assert.equal(walk(grid).filter(node=>node.tag==='img' && node.attrs.onerror).length,0);
assert.ok(grid.textContent.includes('Item'));assert.ok(!grid.textContent.includes('DIRECT_ENTITLEMENT'));
""")


def test_rows_expand_and_keep_state_and_focus_through_progress_renders():
    run_inventory(r"""
state.campaigns={one:campaign()};renderInventory();
assert.equal(content('one').hidden,true);assert.equal(toggle('one').getAttribute('aria-expanded'),'false');
toggle('one').focus();toggle('one').events.click();
assert.equal(content('one').hidden,false);assert.equal(toggle('one').getAttribute('aria-expanded'),'true');
state.campaigns.one.drops[0].current_minutes=11;renderInventory();
assert.equal(content('one').hidden,false);assert.equal(document.activeElement,toggle('one'));
assert.equal(toggle('one').getAttribute('aria-controls'),content('one').id);
toggle('one').events.click();assert.equal(content('one').hidden,true);
""")


def test_shortcut_waits_for_saved_games_before_appending():
    run_inventory(r"""
state.campaigns={one:campaign()};state.settings={};renderInventory();
assert.equal(buttons().length,0);
await addInventoryGame('one',null);assert.equal(requests.length,0);
state.settings.games_to_watch=[];renderInventory();assert.equal(buttons().length,1);
""")


def test_add_appends_once_persists_and_updates_all_campaigns_without_toggling():
    run_inventory(r"""
state.campaigns={one:campaign(),two:campaign('two')};renderInventory();toggle('one').events.click();
const button=buttons()[0];button.focus();let release;
responder=()=>new Promise(resolve=>{release=resolve;});
const pending=addInventoryGame('one',button);
await Promise.resolve();
assert.ok(buttons().every(control=>control.disabled));
await addInventoryGame('two',buttons()[1]);assert.equal(requests.length,1);
assert.deepEqual(JSON.parse(requests[0].options.body),{games_to_watch:['First','Manual','Cyberpunk 2077']});
assert.equal(requests[0].url,'/api/settings');
release({ok:true,json:async()=>({success:true,settings:{games_to_watch:['First','Manual','Cyberpunk 2077']}})});
await pending;assert.equal(buttons().length,0);assert.equal(content('one').hidden,false);
assert.deepEqual(state.settings.games_to_watch,['First','Manual','Cyberpunk 2077']);
assert.equal(document.activeElement,toggle('one'));assert.equal(toasts.length,0);
""")


@pytest.mark.parametrize('failure', ['http', 'application', 'missing', 'network', 'json'])
def test_save_failures_leave_priority_unchanged_and_allow_retry(failure):
    run_inventory(r"""
state.campaigns={one:campaign()};renderInventory();
const failure=__FAILURE__;
responder=async()=>{
 if(failure==='network')throw new Error('private transport details');
 return {ok:failure!=='http',json:async()=>{if(failure==='json')throw new Error('private response');
 return failure==='missing'?{success:true,settings:{games_to_watch:[]}}:{success:false};}};
};
await addInventoryGame('one',buttons()[0]);
assert.deepEqual(state.settings.games_to_watch,['First','Manual']);
assert.equal(buttons().length,1);assert.equal(buttons()[0].disabled,false);
assert.deepEqual(toasts,[['Save failed','error']]);
""".replace('__FAILURE__', json.dumps(failure)))


@pytest.mark.parametrize('local_edit', [False, True])
def test_old_save_response_cannot_overwrite_newer_settings(local_edit):
    run_inventory(r"""
state.campaigns={one:campaign()};renderInventory();let release;
responder=()=>new Promise(resolve=>{release=resolve;});
const pending=addInventoryGame('one',buttons()[0]);
await Promise.resolve();
let saved;
if(__LOCAL__){state.settings.games_to_watch=['Newer'];saved=saveSettings();}
else updateSettingsUI({games_to_watch:['Newer'],language:'French'});
release({ok:true,json:async()=>({success:true,settings:{games_to_watch:['First','Manual','Cyberpunk 2077']}})});
if(__LOCAL__)responder=async()=>({ok:true,json:async()=>({success:true,settings:{games_to_watch:['Newer']}})});
await pending;if(saved)await saved;assert.deepEqual(state.settings.games_to_watch,['Newer']);
""".replace('__LOCAL__', json.dumps(local_edit)))


def test_inventory_translation_keys_match_schema_in_every_locale():
    for path in (ROOT / 'lang').glob('*.json'):
        inventory = json.loads(path.read_text())['gui']['inventory']
        assert set(inventory) == set(GUIInventory.__annotations__), path.name
        for key in ('add_game', 'adding_game', 'add_game_error', 'link_account'):
            assert inventory[key].strip(), (path.name, key)


@pytest.mark.parametrize('edit', ['unrelated', 'remove', 'reorder', 'add'])
@pytest.mark.parametrize('inventory_failure', [False, True])
def test_settings_and_inventory_saves_preserve_ordered_intentions_on_server(edit, inventory_failure):
    run_inventory(r"""
let server={games_to_watch:['A','B'],connection_quality:1};
state.campaigns={one:campaign('one',{game_name:'C'})};updateSettingsUI({...server});
const responses=[];
responder=()=>new Promise(resolve=>{
 const payload=JSON.parse(requests.at(-1).options.body);
 responses.push((fail=false)=>{
  if(fail){resolve({ok:false});return;}
  server={...server,...payload};const saved={...server,games_to_watch:[...server.games_to_watch]};
  updateSettingsUI(saved);resolve({ok:true,json:async()=>({success:true,settings:saved})});
 });
});
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const first=addInventoryGame('one',buttons()[0]);await tick();
assert.equal(requests.length,1);assert.deepEqual(JSON.parse(requests[0].options.body),{games_to_watch:['A','B','C']});
const edit=__EDIT__,failed=__FAILED__;
if(edit==='remove')state.settings.games_to_watch=state.settings.games_to_watch.filter(name=>name!=='B');
if(edit==='reorder')state.settings.games_to_watch=['B','A','C'];
if(edit==='add')state.settings.games_to_watch.push('D');
document.getElementById('connection-quality').value='2';
const second=saveSettings();await tick();assert.equal(requests.length,1,'generic save must wait');
responses.shift()(failed);await tick();assert.equal(requests.length,2);
const expected=edit==='remove'?(failed?['A']:['A','C']):edit==='reorder'?(failed?['B','A']:['B','A','C'])
 :edit==='add'?(failed?['A','B','D']:['A','B','C','D']):(failed?['A','B']:['A','B','C']);
assert.deepEqual(JSON.parse(requests[1].options.body).games_to_watch,expected);
assert.equal(JSON.parse(requests[1].options.body).connection_quality,2);
responses.shift()();await Promise.all([first,second]);
assert.deepEqual(server.games_to_watch,expected);assert.deepEqual(state.settings.games_to_watch,expected);
assert.equal(server.connection_quality,2);assert.equal(toasts.length,failed?1:0);
""".replace('__EDIT__', json.dumps(edit)).replace('__FAILED__', json.dumps(inventory_failure)))


def test_inventory_waits_for_earlier_save_and_rechecks_confirmed_games():
    run_inventory(r"""
state.campaigns={one:campaign('one',{game_name:'C'})};updateSettingsUI({games_to_watch:['A','B']});
const button=buttons()[0];let release;
responder=()=>new Promise(resolve=>{release=resolve;});
const first=saveSettings();await new Promise(resolve=>setImmediate(resolve));
const second=addInventoryGame('one',button);await new Promise(resolve=>setImmediate(resolve));
assert.equal(requests.length,1);
// The earlier save's authoritative response already contains C: no duplicate request.
const saved={games_to_watch:['B','A','C']};updateSettingsUI(saved);
release({ok:true,json:async()=>({success:true,settings:saved})});
await Promise.all([first,second]);
assert.equal(requests.length,1);assert.deepEqual(state.settings.games_to_watch,['B','A','C']);
assert.equal(buttons().length,0);
""")


def test_link_badge_is_separate_from_actions_at_overview_top_right():
    run_inventory(r"""
state.campaigns={one:campaign('one',{linked:false})};renderInventory();
const overview=document.querySelectorAll('.campaign-overview')[0];
const badge=document.querySelectorAll('.campaign-badge')[0];
const actions=document.querySelectorAll('.campaign-actions')[0];
assert.equal(badge.parentElement.parentElement,overview);assert.equal(actions.contains(badge),false);
assert.equal(document.querySelectorAll('.link-account-btn').length,1);
""")


def test_campaign_metadata_appears_once_and_disclosure_remains_accessible():
    run_inventory(r"""
state.campaigns={one:campaign()};renderInventory();
assert.equal(walk(grid).filter(node=>node._text==='Quadra Paintjobs').length,1);
assert.equal(document.querySelectorAll('.campaign-date-range').length,1);
assert.equal(document.querySelectorAll('.campaign-timing').length,0);
assert.equal(document.querySelectorAll('.drop-item-header').length,0);
assert.equal(document.querySelectorAll('.campaign-name-link')[0].attrs.href,'https://www.twitch.tv/drops/campaigns');
assert.equal(toggle('one').getAttribute('aria-label'),'Cyberpunk 2077 — Quadra Paintjobs');
assert.ok(toggle('one').parentElement.contains(document.querySelectorAll('.campaign-badge')[0]));
assert.equal(content('one').hidden,true);toggle('one').events.click();assert.equal(content('one').hidden,false);
state.campaigns.one.drops[0].name='A distinct reward';renderInventory();
assert.equal(document.querySelectorAll('.drop-item-header').length,1);
assert.ok(grid.textContent.includes('A distinct reward'));
""")



def test_campaign_and_link_account_focus_survive_progress_and_link_changes():
    run_inventory(r"""
state.campaigns={one:campaign('one',{linked:false})};renderInventory();
const campaignLink=()=>document.getElementById('inventory-campaign-one');
const accountLink=()=>document.getElementById('inventory-link-one');
campaignLink().focus();state.campaigns.one.drops[0].current_minutes=11;renderInventory();
assert.equal(document.activeElement,campaignLink());
accountLink().focus();renderInventory();assert.equal(document.activeElement,accountLink());
state.campaigns.one.linked=true;renderInventory();assert.equal(document.activeElement,toggle('one'));
""")


def test_whole_overview_toggles_card_but_links_and_actions_do_not():
    run_inventory(r"""
state.campaigns={one:campaign('one',{linked:false})};renderInventory();
const overview=document.querySelectorAll('.campaign-overview')[0];
const title=document.querySelectorAll('.campaign-game-name')[0];
overview.events.click({target:title});assert.equal(content('one').hidden,false);
assert.equal(toggle('one').getAttribute('aria-expanded'),'true');
for(const target of [document.getElementById('inventory-link-one'),document.querySelectorAll('.campaign-name-text')[0],toggle('one')]) {
 overview.events.click({target});assert.equal(content('one').hidden,false);
}
overview.events.click({target:document.querySelectorAll('.campaign-badge')[0]});assert.equal(content('one').hidden,false);
selectionText='14 Oct';overview.events.click({target:title});assert.equal(content('one').hidden,false);selectionText='';
overview.events.click({target:document.querySelectorAll('.campaign-date-range')[0]});assert.equal(content('one').hidden,true);
assert.equal(title.attrs.title,'Cyberpunk 2077');
assert.equal(document.querySelectorAll('.campaign-name-link')[0].attrs.title,'Quadra Paintjobs');
""")


def test_inventory_view_buttons_switch_and_persist_mode_once():
    run_inventory(r"""
const cards=document.getElementById('inventory-view-cards');const list=document.getElementById('inventory-view-list');
setInventoryViewMode(true);await gameWatchWrites.tail;
assert.equal(grid.classList['list-view'],true);
assert.equal(list.getAttribute('aria-pressed'),'true');assert.equal(cards.getAttribute('aria-pressed'),'false');
assert.equal(requests.length,1);assert.equal(JSON.parse(requests[0].options.body).inventory_list_view,true);
setInventoryViewMode(true);await gameWatchWrites.tail;assert.equal(requests.length,1);
setInventoryViewMode(false);await gameWatchWrites.tail;
assert.equal(grid.classList['list-view'],false);assert.equal(cards.getAttribute('aria-pressed'),'true');
assert.equal(JSON.parse(requests[1].options.body).inventory_list_view,false);
""")


def test_card_grid_keeps_columns_stable_and_titles_single_line():
    css = (ROOT / 'web/static/styles.css').read_text(encoding='utf-8')
    html = (ROOT / 'web/index.html').read_text(encoding='utf-8')
    grid_rule = css.split('.inventory-grid {', 1)[1].split('}', 1)[0]
    assert 'column-width' not in grid_rule and '--inventory-card-min: 350px' in grid_rule
    assert 'display: flex' in grid_rule and 'align-items: flex-start' in grid_rule
    column_rule = css.split('.inventory-column {', 1)[1].split('}', 1)[0]
    assert 'flex-direction: column' in column_rule and 'min-width: 0' in column_rule
    list_rule = css.split('.inventory-grid.list-view {', 1)[1].split('}', 1)[0]
    assert 'align-items: stretch' in list_rule
    assert '.campaign-game-name, .campaign-name-text { max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }' in css
    assert 'id="inventory-list-view"' not in html
    assert 'id="inventory-view-cards"' in html and 'id="inventory-view-list"' in html


def test_view_button_before_settings_load_applies_without_saving_defaults():
    run_inventory(r"""
state.settings={};setInventoryViewMode(true);await gameWatchWrites.tail;
assert.equal(grid.classList['list-view'],true);assert.equal(requests.length,0);
""")


def test_narrow_cards_keep_badge_top_right_and_allow_two_line_names():
    css = (ROOT / 'web/static/styles.css').read_text(encoding='utf-8')
    narrow = css.split('@media (max-width: 480px) {\n    /* Narrow cards', 1)[1].split('\n}\n', 1)[0]
    assert 'grid-template-columns: minmax(0, 1fr) 104px' in narrow
    assert 'grid-template-rows: minmax(84px, auto) minmax(96px, auto)' in narrow
    assert 'display: contents' not in narrow  # badge and arrow stay together top-right
    assert 'white-space: normal' in narrow.split('.campaign-badge {', 1)[1].split('}', 1)[0]
    assert '-webkit-line-clamp: 2' in narrow


def test_cards_fill_fixed_columns_so_opening_one_never_moves_others():
    run_inventory(r"""
state.campaigns=Object.fromEntries(['a','b','c','d','e'].map(id=>[id,campaign(id)]));
grid.clientWidth=1240;renderInventory();
const columns=()=>grid.children;const ids=column=>column.children.map(card=>card.children[0].children[0].children[1].children[1].id);
assert.equal(columns().length,3);assert.equal(grid.dataset.columns,'3');
const before=columns().map(ids);
assert.deepEqual(before.map(column=>column.length),[2,2,1]);assert.deepEqual(before,[['inventory-campaign-a','inventory-campaign-d'],['inventory-campaign-b','inventory-campaign-e'],['inventory-campaign-c']]);
toggle('a').events.click();renderInventory();
assert.deepEqual(columns().map(ids),before);assert.equal(content('a').hidden,false);
grid.clientWidth=700;applyInventoryViewMode(false);assert.equal(columns().length,1);
grid.clientWidth=1240;applyInventoryViewMode(true);assert.equal(columns().length,1);
applyInventoryViewMode(false);assert.equal(columns().length,3);
""")


def test_filter_bar_groups_chips_under_translated_headings():
    html = (ROOT / 'web/index.html').read_text(encoding='utf-8')
    bar = html[html.index('<div class="inventory-filters">'):html.index('<div class="inventory-grid"')]
    assert bar.index('id="inventory-game-search"') < bar.index('class="filter-checkboxes"')
    for heading, ids in (('filter-status-heading', ['filter-active', 'filter-upcoming', 'filter-expired', 'filter-finished']),
                         ('filter-rewards-heading', ['filter-benefit-item', 'filter-benefit-badge', 'filter-benefit-emote', 'filter-benefit-other'])):
        group = bar.split(f'aria-labelledby="{heading}"', 1)[1].split('</div>', 1)[0]
        assert all(f'id="{control}"' in group for control in ids)
    assert 'filter-separator' not in bar
    css = (ROOT / 'web/static/styles.css').read_text(encoding='utf-8')
    # Chips keep the native checkbox focusable and only restyle the Inventory bar.
    assert '.inventory-filters .filter-checkbox:has(input:focus-visible)' in css
    assert 'opacity: 0;' in css.split('.inventory-filters .filter-checkbox input[type="checkbox"] {', 1)[1].split('}', 1)[0]
    assert css.index('@supports selector(:has(*))') < css.index('.inventory-filters .filter-checkbox input[type="checkbox"] {')
    assert 'content: "✓" / "";' in css
    # The Mining list action row gains a Reload button and must wrap on phones.
    assert 'flex-wrap: wrap;' in css.split('.filter-actions {', 1)[1].split('}', 1)[0]
    for path in (ROOT / 'lang').glob('*.json'):
        filters = json.loads(path.read_text(encoding='utf-8'))['gui']['inventory']['filters']
        assert filters['status_heading'].strip() and filters['rewards_heading'].strip(), path
