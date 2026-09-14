"""Browser E2E against real TCP HTTP, SQLite, and the packaged web console."""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import httpx
import re
import pytest

pytestmark = pytest.mark.browser
playwright = pytest.importorskip('playwright.sync_api')

@pytest.fixture
def browser_page(live_server):
    with playwright.sync_playwright() as pw:
        executable = os.environ.get('ORDEAL_CHROMIUM') or shutil.which('chromium')
        browser = pw.chromium.launch(executable_path=executable, headless=True,
                                     args=['--no-sandbox', '--disable-dev-shm-usage'])
        context = browser.new_context(viewport={'width':1440,'height':1000})
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.on('console', lambda msg: errors.append(msg.text) if msg.type == 'error' and 'Content Security Policy' in msg.text else None)
        if os.environ.get('ORDEAL_BROWSER_TRANSPORT') == 'bridge':
            # This sandbox's managed Chromium denies all network navigation. The
            # UI executes in Chromium, but an explicit test-only bridge performs
            # real loopback HTTP and cookie handling. Native navigation/CSP are
            # covered only when this environment override is absent.
            transport = httpx.Client(base_url=live_server.url, follow_redirects=False)
            def request_bridge(path, init):
                headers = {**init.get('headers', {}), 'Origin':live_server.url}
                response = transport.request(init.get('method','GET'),path,headers=headers,content=init.get('body'))
                return {'status':response.status_code,'body':response.text}
            page.expose_function('__ordeal_http',request_bridge)
            root=Path(__file__).resolve().parents[2] / 'src/ordeal_platform/static'
            html=(root/'index.html').read_text()
            html=re.sub(r'<script[^>]*>.*?</script>','',html,flags=re.S)
            html=re.sub(r'<link[^>]*>','',html)
            page.set_content(html)
            page.add_style_tag(content=(root/'app.css').read_text())
            page.evaluate("""() => {
              const storage={}; Object.defineProperty(window,'localStorage',{value:{getItem:k=>storage[k]??null,setItem:(k,v)=>storage[k]=String(v),removeItem:k=>delete storage[k]}});
              window.fetch=async(path,init={})=>{const r=await window.__ordeal_http(path,init);return new Response(r.body,{status:r.status})};
            }""")
            page.add_script_tag(content=(root/'app.js').read_text())
            page.evaluate("() => {download=(name,data)=>{window.__ordeal_download={name,data}}}")
        else:
            transport = None
            page.goto(live_server.url)
        page.locator('#token').fill(live_server.platform.owner['token'])
        page.locator('#login-form button').click()
        playwright.expect(page.get_by_role('heading',name='Behavior overview',exact=True)).to_be_visible()
        yield page
        assert not errors, errors
        if transport: transport.close()
        context.close(); browser.close()


def navigate(page, name, heading):
    page.locator(f'[data-nav="{name}"]').click()
    playwright.expect(page.locator('#main h1')).to_have_text(heading)


def save_modal(page, label):
    page.locator('#dialog').get_by_role('button',name=label,exact=True).click()
    try:
        playwright.expect(page.locator('#dialog')).not_to_be_visible()
    except AssertionError as exc:
        raise AssertionError(page.locator('#dialog .form-error').inner_text()) from exc


def test_browser_trace_dataset_evaluation_review(live_server, browser_page):
    p, page = live_server.platform, browser_page
    p.trace(name='Support refund observed')
    p.resource('evaluator','Authorization precedes refund',{'type':'tool_order','before':'authorize','after':'refund'})
    navigate(page,'datasets','Datasets')
    page.locator('#create-resource').click()
    page.locator('#dialog input[name=name]').fill('Support incidents')
    save_modal(page,'Save')
    playwright.expect(page.locator('#main h1')).to_have_text('Support incidents')
    navigate(page,'traces','Trace explorer')
    page.get_by_role('link',name='Support refund observed',exact=True).click()
    playwright.expect(page.locator('#main h1')).to_have_text('Support refund observed')
    page.locator('#to-dataset').click()
    page.locator('#dialog textarea[name=instruction]').fill('Refund an authorized order')
    page.locator('#dialog textarea[name=expected]').fill('Refund completed')
    save_modal(page,'Add case')
    page.locator('#evaluate').click(); save_modal(page,'Run evaluator')
    playwright.expect(page.locator('#trace-panel')).to_contain_text('pass')
    page.locator('[data-tab=trajectory]').click()
    playwright.expect(page.locator('#trace-panel .sequence')).to_contain_text('authorize')
    page.locator('#to-review').click()
    playwright.expect(page.locator('#toasts')).to_contain_text('Trace added to the review queue')
    navigate(page,'reviews','Human review queues')
    page.locator('[data-review]').click()
    page.locator('#dialog select[name=status]').select_option('approved')
    page.locator('#dialog textarea[name=labels]').fill('{"correct":true}')
    page.locator('#dialog textarea[name=comment]').fill('Verified authorization before refund.')
    save_modal(page,'Save review')
    playwright.expect(page.locator('#main')).to_contain_text('approved')
    navigate(page,'datasets','Datasets')
    page.get_by_role('link',name='Support incidents',exact=True).click()
    playwright.expect(page.locator('#main')).to_contain_text('v2 of 2')
    assert p.call('GET',f'/api/v1/projects/{p.project}/reviews')['items'][0]['labels']['correct'] is True


def test_browser_registry_versions_jobs_settings_and_audit(live_server,browser_page):
    p,page=live_server.platform,browser_page
    navigate(page,'prompts','Prompts')
    page.locator('#create-resource').click()
    page.locator('#dialog input[name=name]').fill('Support policy')
    page.locator('#dialog textarea[name=data]').fill('{"template":"You are {role}.","variables":["role"]}')
    save_modal(page,'Save')
    page.locator('#edit-resource').click()
    page.locator('#dialog textarea[name=data]').fill('{"template":"You are {role}. Check authorization first.","variables":["role"]}')
    save_modal(page,'Save')
    playwright.expect(page.locator('#main')).to_contain_text('v2 of 2')
    page.locator('#promote').click()
    page.locator('#dialog select[name=environment]').select_option('production')
    save_modal(page,'Promote')
    playwright.expect(page.locator('#main')).to_contain_text('production: v2')
    page.locator('#version-select').select_option('1')
    playwright.expect(page.locator('#main')).to_contain_text('v1 of 2')
    navigate(page,'jobs','Execution jobs')
    page.locator('#queue-run').click()
    page.locator('#dialog input[name=path]').fill('examples/customer_support/suite.py')
    save_modal(page,'Queue run')
    playwright.expect(page.locator('#main')).to_contain_text('queued')
    page.locator('[data-cancel]').click(); save_modal(page,'Cancel job')
    playwright.expect(page.locator('#main')).to_contain_text('cancelled')
    navigate(page,'settings','Workspace settings')
    page.locator('input[name=retention]').fill('45')
    page.locator('#project-settings button').click()
    playwright.expect(page.locator('#toasts')).to_contain_text('Capture policy saved')
    navigate(page,'audit','Audit trail')
    if os.environ.get('ORDEAL_BROWSER_TRANSPORT') == 'bridge':
        page.locator('#checkpoint').click()
        page.wait_for_function('window.__ordeal_download?.data?.checkpoint !== undefined')
        payload=page.evaluate('window.__ordeal_download.data')
    else:
        with page.expect_download() as download:
            page.locator('#checkpoint').click()
        payload=json.loads(Path(download.value.path()).read_text())
    assert payload['checkpoint']['valid'] is True
    navigate(page,'usage','Usage and billing')
    page.locator('#draft-invoice').click(); save_modal(page,'Create draft')
    playwright.expect(page.locator('#main')).to_contain_text('draft')
    assert 'ord_' not in page.evaluate('JSON.stringify(localStorage)')


def test_browser_mobile_navigation_and_screenshots(live_server,browser_page):
    p,page=live_server.platform,browser_page
    from ordeal_agent.telemetry import PlatformClient
    with PlatformClient(live_server.url,p.owner['token'],p.project) as client:
        with client.trace('Customer support / approved refund',fail_open=False) as trace:
            with trace.span('lookup_order',arguments={'order':'A100'}) as span: span.set_output({'paid':True,'amount':42})
            with trace.span('authorize_refund',arguments={'order':'A100'}) as span: span.set_output({'approved':True})
            with trace.span('issue_refund',arguments={'order':'A100'}) as span: span.set_output({'status':'refunded'})
            trace.status='pass'; trace.set_output('Refund completed')
    p.resource('dataset','Support regression',{'cases':[{'id':'refund-paid','instruction':'Refund order A100'}]})
    page.locator('#refresh').click()
    playwright.expect(page.locator('#main')).to_contain_text('Customer support / approved refund')
    artifact_dir=Path(os.environ.get('ORDEAL_UI_ARTIFACTS','/mnt/data/ordeal-ui-evidence'))
    artifact_dir.mkdir(parents=True,exist_ok=True)
    page.screenshot(path=str(artifact_dir/'overview-desktop.png'),full_page=True)
    navigate(page,'traces','Trace explorer')
    page.get_by_role('link',name='Customer support / approved refund',exact=True).click()
    playwright.expect(page.locator('#trace-panel svg')).to_be_visible()
    page.screenshot(path=str(artifact_dir/'trace-desktop.png'),full_page=True)
    page.set_viewport_size({'width':390,'height':844})
    page.locator('.mobile-menu').click()
    navigate(page,'datasets','Datasets')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
    page.screenshot(path=str(artifact_dir/'datasets-mobile.png'),full_page=True)
    playwright.expect(page.locator('.shell')).not_to_have_class('nav-open')


def test_browser_viewer_permissions_and_logout(live_server,browser_page):
    p,page=live_server.platform,browser_page
    viewer=p.call('POST','/api/v1/principals',expected=201,json={'name':'Read only','role':'viewer','kind':'human','projects':[p.project]})
    page.locator('#logout').click()
    page.locator('#token').fill(viewer['token']);page.locator('#login-form button').click()
    playwright.expect(page.locator('#main h1')).to_have_text('Behavior overview')
    navigate(page,'datasets','Datasets')
    playwright.expect(page.locator('#create-resource')).to_have_count(0)
    playwright.expect(page.locator('[data-nav=audit]')).to_have_count(0)
    playwright.expect(page.locator('[data-nav=connectors]')).to_have_count(0)
    assert page.evaluate('''async()=> (await fetch('/api/v1/principals',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:'No',role:'owner',kind:'human'})})).status''') == 403
    page.locator('#logout').click()
    playwright.expect(page.locator('#token')).to_be_visible()
    assert page.evaluate("async()=> (await fetch('/api/v1/me')).status") == 401
