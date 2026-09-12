import pytest

from ordeal_tui import CommandPalette, OrdealTUI, meter


def fixture_response(path):
    if path == "/api/summary":
        return {"runs":1,"worlds":1,"scenarios":2,"suites":1,"constraints":1,"online_workers":2,"queue_backend":"local"}
    if path == "/api/runs":
        return [{"id":7,"name":"candidate-7","status":"completed","payload":{"pass_count":2,"fail_count":0,"pass_rate":1,"trials":[{"id":"t1","scenario":"safe-retry","seed":41,"passed":True}],"run_fingerprint":"abc123"}}]
    if path == "/api/agents": return [{"name":"coding-agent","payload":{}}]
    if path == "/api/suites": return [{"name":"trusted-suite","payload":{"scenarios":["a","b"]}}]
    raise AssertionError(path)


def test_meter_is_bounded_and_deterministic():
    assert len(meter(5,10,20)) == 20
    assert meter(50,10,8).count("█") == 8
    assert meter(1,0,8).count("█") == 0


@pytest.mark.asyncio
async def test_cockpit_navigation_and_live_data(monkeypatch):
    monkeypatch.setattr("ordeal_tui.Client.request",lambda self,path,*args,**kwargs:fixture_response(path))
    app=OrdealTUI("http://ordeal.test",None)
    async with app.run_test(size=(120,40)) as pilot:
        await pilot.pause(.3)
        assert app.online and len(app.runs)==1
        assert "VERIFIED" in str(app.query_one("#gate-verdict").render())
        await pilot.press("2"); await pilot.pause(); assert app.active_view=="runs"
        await pilot.press("3"); await pilot.pause(); assert app.active_view=="campaign"
        await pilot.press(":"); await pilot.pause(); assert isinstance(app.screen,CommandPalette)
        await pilot.press("escape")


@pytest.mark.asyncio
async def test_compact_breakpoint(monkeypatch):
    monkeypatch.setattr("ordeal_tui.Client.request",lambda self,path,*args,**kwargs:fixture_response(path))
    app=OrdealTUI("http://ordeal.test",None)
    async with app.run_test(size=(68,32)) as pilot:
        await pilot.pause(.2)
        assert app.screen.has_class("compact")
