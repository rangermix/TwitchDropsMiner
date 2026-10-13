import json
import subprocess

import pytest

from tests.javascript_helpers import APP_JS, NODE, extract_javascript_function


pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend tests")


def run_javascript(names, body):
    source = APP_JS.read_text(encoding="utf-8")
    functions = []
    for name in names:
        function = extract_javascript_function(source, name)
        if f"async function {name}(" in source:
            function = "async " + function
        functions.append(function)
    result = subprocess.run(
        [NODE, "-"], input="\n".join(functions) + body, text=True, capture_output=True, check=True
    )
    return json.loads(result.stdout)


def test_history_translations_use_text_and_refresh_dynamic_states():
    result = run_javascript(["historyText", "translateHistory", "updateHistoryCount"], """
const title = {dataset: {historyKey: 'title'}};
const input = {dataset: {historyPlaceholder: 'filter_game'}, attrs: {}, setAttribute(k,v) {this.attrs[k]=v;}};
const count = {};
const document = {querySelectorAll: s => s === '[data-history-key]' ? [title] : [input], getElementById: () => count};
const state = {translations: {gui: {history: {title:'历史', filter_game:'游戏', filtered_count:'{shown}/{total}', count:'总数：{total}'}}}};
const historyTotal = 10;
const historyAllEntries = [{}, {}];
const historyCurrentPage = 0;
let historyMessage = null;
const historyStatsData = {};
let pages = 0, stats = 0;
function renderHistoryPage() { pages++; }
function renderHistoryPagination() {}
function renderHistoryStats() { stats++; }
function setHistoryTbodyMessage() {}
translateHistory();
const first = {title:title.textContent, placeholder:input.placeholder, label:input.attrs['aria-label'], count:count.textContent};
state.translations.gui.history.title = '<img src=x onerror=alert(1)>';
state.translations.gui.history.filtered_count = '{shown} shown of {total}';
translateHistory();
process.stdout.write(JSON.stringify({first, literal:title.textContent, count:count.textContent, pages, stats}));
""")
    assert result == {
        "first": {"title": "历史", "placeholder": "游戏", "label": "游戏", "count": "2/10"},
        "literal": "<img src=x onerror=alert(1)>", "count": "2 shown of 10", "pages": 2, "stats": 2,
    }


def test_history_load_clamps_page_and_ignores_old_filter_response():
    result = run_javascript(["loadHistory"], """
const filter = {value: '原神'};
const since = {value: '2026-09-01'};
const document = {getElementById: id => id === 'history-filter-game' ? filter : since};
let historyRequestId = 0, historyCurrentPage = 7, historyTotal = 0, historyAllEntries = [];
const HISTORY_PAGE_SIZE = 50;
const pending = [], urls = [], pages = [];
function fetch(url) { urls.push(url); return new Promise(resolve => pending.push(resolve)); }
function updateHistoryCount() {}
function renderHistoryPage(page) { pages.push(page); }
function renderHistoryPagination() {}
function setHistoryTbodyMessage() {}
(async () => {
 const old = loadHistory();
 filter.value = 'Rust';
 const latest = loadHistory();
 pending[1]({ok:true, json:async()=>({total:1,entries:[{id:'latest'}]})});
 await latest;
 pending[0]({ok:true, json:async()=>({total:100,entries:[{id:'old'}]})});
 await old;
 const params = new URL(urls[0], 'http://test').searchParams;
 process.stdout.write(JSON.stringify({entries:historyAllEntries,page:historyCurrentPage,total:historyTotal,pages,
 game:params.get('game'),since:params.get('since')}));
})();
""")
    assert result == {"entries": [{"id": "latest"}], "page": 0, "total": 1, "pages": [0],
                      "game": "原神", "since": "2026-09-01"}


def test_clear_history_requires_confirmation_and_successful_response():
    result = run_javascript(["clearHistory"], """
let historyRequestId = 0, historyCurrentPage = 2, historyTotal = 1, historyAllEntries = [{id:'keep'}];
let historyStatsData = null, historyStatsVisible = false, confirm = false, ok = false, calls = 0;
const window = {confirm: () => confirm};
function historyText(key) { return key; }
const alerts = [];
function alert(message) { alerts.push(message); }
async function fetch() { calls++; return {ok,status:500}; }
function updateHistoryCount() {}
function setHistoryTbodyMessage() {}
function renderHistoryPagination() {}
(async () => {
 await clearHistory();
 const cancelledCalls = calls;
 confirm = true;
 await clearHistory();
 const afterFailure = [...historyAllEntries];
 ok = true;
 await clearHistory();
 process.stdout.write(JSON.stringify({cancelledCalls,afterFailure,alerts,calls,
 entries:historyAllEntries,total:historyTotal,page:historyCurrentPage,historyRequestId,historyStatsData}));
})();
""")
    assert result == {"cancelledCalls": 0, "afterFailure": [{"id": "keep"}], "alerts": ["clear_error"],
                      "calls": 2, "entries": [], "total": 0, "page": 0, "historyRequestId": 1,
                      "historyStatsData": {"total_drops": 0, "by_game": {}, "by_month": {}}}
