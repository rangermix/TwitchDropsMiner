"""Wanted queue: temporary drag ordering, reset, failure recovery and safe text."""

import pytest

from tests.javascript_helpers import NODE
from tests.test_telegram_frontend import run_javascript


pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend tests")

FUNCTIONS = ['makeElement', 'makeImageElement', 'wantedText', 'updateGameOrder', 'setWantedOrderError',
             'pointerPastMiddle',
             'saveWantedOrder', 'handleWantedDragStart',
             'handleWantedDragEnd', 'renderWantedItems', 'handleDragStart', 'handleWantedDragOver',
             'dragOverRows', 'handleDrop', 'handleWantedTouchStart', 'handleWantedTouchMove',
             'handleWantedTouchEnd', 'flushPendingWantedItems']

DOM = r"""
const assert = require('node:assert/strict');
class Node {
 constructor(tag){this.tag=tag;this.children=[];this.attrs={};this.events={};this.dataset={};this.classes=new Set();
  this._text='';this.disabled=false;this.hidden=false;this.parentNode=null;this.style={};this.draggable=undefined;
  this.classList={add:n=>this.classes.add(n),remove:n=>this.classes.delete(n)};}
 setAttribute(k,v){this.attrs[k]=String(v);if(k==='id')this.id=String(v);if(k==='class')String(v).split(' ').forEach(n=>this.classes.add(n));}
 set className(v){this.classes=new Set();this.setAttribute('class',v);}
 set innerHTML(v){assert.equal(v,'');this.children.forEach(c=>{c.parentNode=null;});this.children=[];this._text='';}
 set textContent(v){this._text=String(v);this.children=[];}
 get textContent(){return this._text+this.children.map(c=>c.textContent).join('');}
 appendChild(c){c.parentNode=this;this.children.push(c);return c;}
 append(...cs){cs.forEach(c=>this.appendChild(c));}
 replaceChildren(...cs){this.children=[];this._text='';cs.forEach(c=>this.appendChild(c));}
 addEventListener(t,f){this.events[t]=f;}
 getBoundingClientRect(){return this.rect;}
 closest(sel){let n=this;while(n){if(n.classes?.has(sel.slice(1)))return n;n=n.parentNode;}return null;}
 insertBefore(c,before){this.children=this.children.filter(x=>x!==c);c.parentNode=this;
  const i=before?this.children.indexOf(before):-1;i<0?this.children.push(c):this.children.splice(i,0,c);}
 get nextSibling(){const s=this.parentNode.children;return s[s.indexOf(this)+1]||null;}
 get value(){return this._value??this.attrs.value??'';} set value(v){this._value=String(v);}
 querySelectorAll(sel){const all=this.children.flatMap(c=>[c,...c.querySelectorAll(sel)]);return all.filter(n=>n.classes.has(sel.slice(1)));}
}
const root=new Node('root');
for(const id of ['wanted-items-list','wanted-order-note','wanted-order-reset','wanted-order-error'])root.appendChild(Object.assign(new Node('div'),{id}));
const walk=n=>[n,...n.children.flatMap(walk)];
let pointAt=null;
const document={createElement:t=>new Node(t),getElementById:id=>walk(root).find(n=>n.id===id)||null,elementFromPoint:()=>pointAt};
const state={translations:{gui:{wanted:{none:'Nothing',temporary:'<b>Temporary</b>',drag_hint:'Drag hint',
 reset:'Reset order',save_error:'<img src=x> Failed'}}}};
let draggedElement=null;
let lastWantedTree=[];let pendingWantedTree=null;let wantedDragActive=false;let wantedOrderBusy=false;
let gameOrder={active:false,games:[]};
const WANTED_TOUCH_HOLD_MS=5;let wantedTouch=null;
const socket={emits:[],emit(name){this.emits.push(name);}};
const window={innerHeight:800,scrolls:[],scrollBy(x,y){this.scrolls.push(y);}};
const tree=['Alpha','Beta','Gamma'].map(name=>({game_name:name,game_icon:null,
 campaigns:[{name:name+' campaign',url:'https://www.twitch.tv/drops/campaigns',drops:[{name:'Drop',benefits:['Reward']}]}]}));
const list=()=>document.getElementById('wanted-items-list');
const names=()=>list().children.map(group=>group.dataset.game);
const badges=()=>list().children.map(group=>group.querySelectorAll('.wanted-game-index')[0].textContent);
// Simulate a native drag of row `from` onto row `to` through the real handlers.
async function drag(from,to){
 const row=list().children.find(group=>group.dataset.game===from);
 const target=list().children.find(group=>group.dataset.game===to);
 row.events.dragstart(event(row));
 target.events.dragover({target,preventDefault(){},dataTransfer:{}});
 row.events.dragend(event(row));
 await new Promise(r=>setTimeout(r));
}
let calls=[];let mode='ok';
async function fetch(url,options){
 assert.equal(url,'/api/wanted-order');
 const body=JSON.parse(options.body);calls.push(body.games);
 assert.equal(wantedOrderBusy,true);assert.equal(document.getElementById('wanted-order-reset').disabled,true);
 if(mode==='network')throw new Error('offline');
 return {ok:mode!=='http',json:async()=>({success:mode!=='application',active:body.games.length>0,games:body.games})};
}
const event=target=>({target,currentTarget:target,dataTransfer:{setData(){}},preventDefault(){this.prevented=true;}});
"""


def test_render_drag_reorder_reset_and_recover():
    run_javascript(FUNCTIONS, DOM + r"""
(async()=>{
 updateGameOrder({active:false,games:[]});
 renderWantedItems(tree);
 assert.deepEqual(names(),['Alpha','Beta','Gamma']);
 assert.deepEqual(badges(),['#1','#2','#3']);
 assert.equal(document.getElementById('wanted-order-note').textContent,'Drag hint');
 assert.equal(document.getElementById('wanted-order-reset').hidden,true);
 assert.equal(list().children[0].draggable,true);
 assert.equal(list().querySelectorAll('.wanted-position-input').length,0,'drag only, no number boxes');
 assert.deepEqual(list().children.map(g=>g.querySelectorAll('.wanted-drag-handle')[0]?.textContent),['☰','☰','☰'],'drag cue on every card');
 const link=walk(list()).find(n=>n.tag==='a');assert.equal(link.attrs.draggable,'false');

 // Dragging Gamma onto Alpha shows the new order at once, then saves it.
 await drag('Gamma','Alpha');
 assert.deepEqual(calls.at(-1),['Gamma','Alpha','Beta']);
 assert.deepEqual(names(),['Gamma','Alpha','Beta']);
 assert.deepEqual(badges(),['#1','#2','#3'],'badges renumbered');
 assert.equal(document.getElementById('wanted-order-note').textContent,'<b>Temporary</b>');
 assert.equal(document.getElementById('wanted-order-reset').hidden,false);
 assert.equal(wantedOrderBusy,false);

 // A drag is ignored while a save is pending.
 wantedOrderBusy=true;const row=list().children[2];const pending=event(row);row.events.dragstart(pending);
 assert.ok(pending.prevented);wantedOrderBusy=false;

 for(const failure of ['http','network','application']){
  mode=failure;const before=names();
  await drag(before.at(-1),before[0]);
  assert.deepEqual(names(),before,'previous order restored on '+failure);
  const error=document.getElementById('wanted-order-error');
  assert.equal(error.hidden,false);assert.equal(error.textContent,'<img src=x> Failed');assert.equal(error.children.length,0);
  assert.equal(wantedOrderBusy,false);
  assert.equal(socket.emits.at(-1),'get_wanted_items','refetch the real queue after a failure');
 }
 // A newer server queue that arrives during a failing save is never overwritten.
 mode='http';const save=saveWantedOrder(['Beta','Alpha','Gamma']);
 renderWantedItems([tree[2],tree[1],tree[0]]);await save;
 assert.deepEqual(names(),['Gamma','Beta','Alpha']);

 // Reset clears the temporary order and the error.
 mode='ok';await saveWantedOrder([]);
 assert.deepEqual(calls.at(-1),[]);
 assert.equal(document.getElementById('wanted-order-reset').hidden,true);
 assert.equal(document.getElementById('wanted-order-error').hidden,true);
})().catch(error=>{console.error(error);process.exit(1);});
""")


def test_drag_defers_live_updates_and_saves_only_real_changes():
    run_javascript(FUNCTIONS, DOM + r"""
(async()=>{
 renderWantedItems(tree);
 const [alpha,beta]=list().children;

 // Links and artwork inside a row never start a row drag.
 const inner=event(alpha);inner.target=alpha.children[0];alpha.events.dragstart(inner);
 assert.ok(inner.prevented);assert.equal(draggedElement,null);

 // A live update during a drag is held; an unchanged drop applies it without saving.
 alpha.events.dragstart(event(alpha));
 renderWantedItems([...tree].reverse());
 assert.deepEqual(names(),['Alpha','Beta','Gamma'],'rows kept under the drag');
 alpha.events.dragend(event(alpha));
 assert.equal(calls.length,0);
 assert.deepEqual(names(),['Gamma','Beta','Alpha']);

 // Links or images dragged from elsewhere never move queue rows or get accepted.
 assert.equal(draggedElement,null,'drag state cleared on drag end');
 let accepted=false;const foreign={target:list().children[0],preventDefault(){accepted=true;},dataTransfer:{}};
 list().children[2].events.dragover(foreign);
 assert.equal(accepted,false);assert.deepEqual(names(),['Gamma','Beta','Alpha']);

 // A real move saves the new order.
 const [first,second]=list().children;
 first.events.dragstart(event(first));
 list().children.splice(0,2,second,first);
 first.events.dragend(event(first));
 await new Promise(r=>setTimeout(r));
 assert.deepEqual(calls,[['Beta','Gamma','Alpha']]);
})().catch(error=>{console.error(error);process.exit(1);});
""")


def test_touch_press_and_hold_drags_while_quick_swipes_scroll():
    run_javascript(FUNCTIONS, DOM + r"""
const touch=(row,type,target=row,x=0,y=0)=>{const e={type,target,currentTarget:row,touches:[{clientX:x,clientY:y}],
 prevented:false,preventDefault(){this.prevented=true;}};row.events[type](e);return e;};
const hold=()=>new Promise(r=>setTimeout(r,20));
(async()=>{
 renderWantedItems(tree);
 const row=name=>list().children.find(group=>group.dataset.game===name);

 // A quick swipe is a scroll: no drag, no blocked page movement, no save.
 touch(row('Gamma'),'touchstart');
 const swipe=touch(row('Gamma'),'touchmove',row('Gamma'),0,40);
 assert.equal(swipe.prevented,false);await hold();
 assert.equal(wantedDragActive,false);touch(row('Gamma'),'touchend');assert.equal(calls.length,0);

 // Links stay tappable and never start a drag.
 const link=walk(row('Alpha')).find(n=>n.tag==='a');
 link.closest=sel=>sel==='a'?link:null;
 touch(row('Alpha'),'touchstart',link);await hold();assert.equal(wantedDragActive,false);

 // Press and hold, then drag Gamma over Alpha: the page stays still and the order saves.
 touch(row('Gamma'),'touchstart');await hold();
 assert.equal(wantedDragActive,true);
 pointAt=row('Alpha').children[0];
 const move=touch(row('Gamma'),'touchmove',row('Gamma'),5,5);
 assert.equal(move.prevented,true);
 assert.ok(window.scrolls.includes(-12),'auto-scrolls near the top edge');
 assert.deepEqual(names(),['Gamma','Alpha','Beta']);
 const end=touch(row('Gamma'),'touchend');assert.equal(end.prevented,true);
 await new Promise(r=>setTimeout(r));
 assert.deepEqual(calls,[['Gamma','Alpha','Beta']]);
 assert.equal(wantedDragActive,false);

 // An interrupted hold restores the order shown before it and saves nothing.
 touch(row('Beta'),'touchstart');await hold();
 pointAt=row('Gamma').children[0];touch(row('Beta'),'touchmove',row('Beta'),5,5);
 assert.equal(names()[0],'Beta');
 touch(row('Beta'),'touchcancel');
 assert.deepEqual(names(),['Gamma','Alpha','Beta']);assert.equal(calls.length,1);assert.equal(wantedDragActive,false);
})().catch(error=>{console.error(error);process.exit(1);});
""")


def test_rows_of_different_heights_do_not_flip_under_a_still_pointer():
    run_javascript(FUNCTIONS, DOM + r"""
(async()=>{
 renderWantedItems(tree);
 // One column: a short card A, a tall card B and C below them.
 const layout=()=>{let top=0;for(const [i,g] of list().children.entries()){const h=g.dataset.game==='Beta'?400:100;
  g.rect={top,left:0,width:300,height:h};top+=h;}};
 layout();
 const [alpha,beta]=list().children;
 alpha.events.dragstart(event(alpha));
 const over=(y)=>{beta.events.dragover({target:beta,clientX:150,clientY:y,preventDefault(){},dataTransfer:{}});layout();return names().join('');};
 // Upper half of B: not past its middle yet, nothing moves.
 assert.equal(over(130),'AlphaBetaGamma');assert.equal(over(140),'AlphaBetaGamma');
 // Past the middle: A moves below B once and stays there while the pointer is still.
 assert.equal(over(310),'BetaAlphaGamma');
 for(const y of [310,320,330,320])assert.equal(over(y),'BetaAlphaGamma');
 alpha.events.dragend(event(alpha));await new Promise(r=>setTimeout(r));
 assert.deepEqual(calls,[['Beta','Alpha','Gamma']]);

 // A native long-press drag is refused while our touch hold runs.
 wantedTouch={active:false};const native=event(list().children[0]);list().children[0].events.dragstart(native);
 assert.ok(native.prevented);wantedTouch=null;
})().catch(error=>{console.error(error);process.exit(1);});
""")


def test_touch_hold_keeps_its_row_when_live_updates_arrive_before_activation():
    run_javascript(FUNCTIONS, DOM + r"""
const touch=(row,type,y=0)=>{const e={type,target:row,currentTarget:row,touches:[{clientX:0,clientY:y}],
 prevented:false,preventDefault(){this.prevented=true;}};row.events[type](e);return e;};
(async()=>{
 renderWantedItems(tree);
 const originalRows=[...list().children];
 const gamma=originalRows[2];
 touch(gamma,'touchstart');
 renderWantedItems([tree[0],tree[2]]);
 renderWantedItems([...tree].reverse());
 assert.deepEqual(list().children,originalRows,'updates must preserve the pending hold source');
 assert.equal(lastWantedTree,tree);
 await new Promise(r=>setTimeout(r,20));
 assert.equal(wantedDragActive,true);
 assert.equal(draggedElement,gamma);
 assert.equal(gamma.parentNode,list(),'the active source must remain in the visible queue');
 assert.ok(gamma.classes.has('dragging'));
 // With no move to save, ending the hold applies the newest deferred tree.
 touch(gamma,'touchend');
 assert.deepEqual(names(),['Gamma','Beta','Alpha']);
 assert.equal(pendingWantedTree,null);
 assert.equal(wantedTouch,null);
 assert.equal(wantedDragActive,false);
 assert.equal(draggedElement,null);
 assert.equal(calls.length,0);
})().catch(error=>{console.error(error);process.exit(1);});
""")


def test_pending_touch_updates_flush_on_scroll_release_cancel_and_stale_timers():
    run_javascript(FUNCTIONS, DOM + r"""
const callbacks=[];
const cleared=new Set();
globalThis.setTimeout=callback=>{callbacks.push(callback);return callback;};
globalThis.clearTimeout=timer=>cleared.add(timer);
const touch=(row,type,y=0)=>{const e={type,target:row,currentTarget:row,touches:[{clientX:0,clientY:y}],
 prevented:false,preventDefault(){this.prevented=true;}};row.events[type](e);return e;};
for(const end of ['touchend','touchcancel','scroll']){
 renderWantedItems(tree);
 const alpha=list().children[0];
 touch(alpha,'touchstart');
 const oldTimer=callbacks.at(-1);
 renderWantedItems([...tree].reverse());
 const finished=end==='scroll'?touch(alpha,'touchmove',40):touch(alpha,end);
 assert.equal(finished.prevented,false,'a pending hold must not prevent ordinary scrolling or tapping');
 assert.ok(cleared.has(oldTimer));
 assert.equal(wantedTouch,null);
 assert.equal(pendingWantedTree,null);
 assert.deepEqual(names(),['Gamma','Beta','Alpha'],'flush the newest update after '+end);
 // A callback already queued before cancellation must not restart its old gesture,
 // even if the user has begun another hold in the meantime.
 const beta=list().children[1];
 touch(beta,'touchstart');
 const current=wantedTouch;
 oldTimer();
 assert.equal(wantedTouch,current);
 assert.equal(wantedDragActive,false);
 assert.equal(draggedElement,null);
 callbacks.at(-1)();
 assert.equal(draggedElement,beta);
 touch(beta,'touchcancel');
 assert.equal(wantedDragActive,false);
 assert.equal(draggedElement,null);
 assert.equal(calls.length,0);
}
""")
