"""Display wrappers may use low descriptors before invoking Chromium."""

import json
import os
import sys

import pytest

from src.auth.server_renewal import OwnedChromium
from src.auth.session_bundle import SessionError
from tests.test_server_renewal import FAKE_CHROMIUM


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="POSIX descriptor transport")
@pytest.mark.parametrize("closed", [(3,), (3, 4)])
async def test_display_wrapper_can_close_low_fds_without_breaking_browser(tmp_path, monkeypatch, closed):
    executable = tmp_path / "fake-chromium"
    executable.write_text(f"#!{sys.executable}\n{FAKE_CHROMIUM}")
    executable.chmod(0o700)
    wrapper = tmp_path / "fake-xvfb-run"
    wrapper.write_text(
        f"#!{sys.executable}\n"
        "import os,sys\n"
        f"for fd in {closed!r}:\n"
        " try: os.close(fd)\n"
        " except OSError: pass\n"
        "command=sys.argv[sys.argv.index('-s')+2:]\n"
        "os.execvp(command[0],command)\n"
    )
    wrapper.chmod(0o700)
    monkeypatch.setattr("src.auth.server_renewal.shutil.which", lambda _: str(wrapper))
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    before = set(os.listdir("/dev/fd"))
    async with OwnedChromium(str(executable), startup_timeout=2).start() as browser, browser.target() as page:
        result = await page.command("Runtime.evaluate", {"expression": "'fixture'"})
        assert result["result"]["value"] == "fake page"
    report = json.loads((tmp_path / "launch.json").read_text())
    assert "--headless=new" not in report["argv"]
    assert "--remote-debugging-pipe" in report["argv"]
    assert report["fds"][:5] == [0, 1, 2, 3, 4]
    assert len(report["fds"]) <= 6
    assert set(os.listdir("/dev/fd")) == before
    assert not list(tmp_path.glob("tdm-renew-*"))


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="POSIX descriptor transport")
@pytest.mark.parametrize("failure", [1, 2])
async def test_partial_descriptor_duplication_failure_closes_all_owned_fds(tmp_path, monkeypatch, failure):
    import fcntl

    actual, calls = fcntl.fcntl, 0

    def duplicate(fd, operation, minimum):
        nonlocal calls
        calls += 1
        if calls == failure:
            raise OSError("private diagnostic detail")
        return actual(fd, operation, minimum)

    monkeypatch.setattr("src.auth.server_renewal.shutil.which", lambda _: "fake-xvfb-run")
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    monkeypatch.setattr(fcntl, "fcntl", duplicate)
    before = set(os.listdir("/dev/fd"))
    with pytest.raises(SessionError, match="BROWSER_START") as caught:
        async with OwnedChromium("not-launched").start():
            pytest.fail("descriptor allocation failure must not start a browser")
    assert calls == failure
    assert "private" not in str(caught.value)
    assert set(os.listdir("/dev/fd")) == before
    assert not list(tmp_path.glob("tdm-renew-*"))
