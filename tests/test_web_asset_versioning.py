import asyncio

from fastapi.responses import HTMLResponse

from src.version import __version__
from src.web.app import serve_index


def test_index_revalidates_and_versions_local_assets():
    response = asyncio.run(serve_index())

    assert isinstance(response, HTMLResponse)
    assert response.headers["cache-control"] == "no-cache"

    body = response.body.decode()
    assert f'/static/styles.css?v={__version__}' in body
    assert f'/static/app.js?v={__version__}' in body
    assert "__APP_VERSION__" not in body
