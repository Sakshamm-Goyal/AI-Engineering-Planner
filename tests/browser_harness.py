"""Opt-in in-memory browser harness for environments that prohibit URL navigation.

Chromium still renders the real compiled HTML/CSS/JS and receives real browser
interactions. A Python binding bridges fetch() to FastAPI's TestClient. Clipboard,
file downloads and the single test route override are simulated, not OS/network
integrations. No administrator policies are modified or disabled.
"""
import base64
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from planner.app import create_app
from planner.config import ROOT, Settings
from tests.conftest import FixtureProvider

BRIDGE = r"""
window.__downloads = [];
window.__clipboard = '';
Object.defineProperty(navigator, 'clipboard', {configurable:true, value:{
  writeText:async text=>{window.__clipboard=text;}, readText:async()=>window.__clipboard
}});
const blobMap = new Map();
const createBlobUrl = URL.createObjectURL.bind(URL);
URL.createObjectURL = blob => {const url=createBlobUrl(blob);blobMap.set(url,blob);return url;};
const originalClick = HTMLAnchorElement.prototype.click;
HTMLAnchorElement.prototype.click = function() {
  if (this.download && blobMap.has(this.href)) {
    const filename=this.download;
    blobMap.get(this.href).text().then(text=>window.__downloads.push({filename,text}));
    return;
  }
  return originalClick.call(this);
};
window.fetch = async (url, init={}) => {
  const request={path:new URL(String(url),'http://testserver').pathname,method:init.method||'GET',headers:init.headers||{},body:null,fields:[],files:[]};
  if (init.body instanceof FormData) {
    for (const [name,value] of init.body.entries()) {
      if (value instanceof File) {
        const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=reject;reader.readAsDataURL(value);});
        request.files.push({field:name,name:value.name,mime:value.type,data});
      } else request.fields.push([name,String(value)]);
    }
  } else if (init.body != null) request.body=String(init.body);
  const result=await window.__backend(request);
  return new Response(result.body,{status:result.status,headers:result.headers});
};
"""


class MemoryDownload:
    def __init__(self, record):
        self.suggested_filename = record["filename"]
        self.text = record["text"]
    def save_as(self, path):
        Path(path).write_text(self.text, encoding="utf-8")


class DownloadEvent:
    def __init__(self, page):
        self.page = page
        self.before = page.evaluate("window.__downloads.length")
        self.value = None
    def __enter__(self): return self
    def __exit__(self, *args):
        self.page.wait_for_function(f"window.__downloads.length > {self.before}")
        self.value = MemoryDownload(self.page.evaluate("window.__downloads.at(-1)"))


class LocalPageHarness:
    def __init__(self, page):
        self._page = page
        self._routes = {}
        self._client = None
        self._page.expose_function("__backend", self._request)

    def __getattr__(self, key):
        return getattr(self._page, key)

    @property
    def context(self):
        return SimpleNamespace(grant_permissions=lambda *args, **kwargs: None)

    def goto(self, url):
        if self._client:
            self._client.__exit__(None, None, None)
        configured = "fixture" in url
        settings = Settings(_env_file=None,
            azure_openai_endpoint="https://fixture.openai.azure.com" if configured else "",
            azure_openai_api_key="fixture-not-a-real-key" if configured else "",
            azure_openai_deployment="test-fixture-not-live" if configured else "gpt-5.6-luna")
        self._client = TestClient(create_app(settings, provider=FixtureProvider()))
        self._client.__enter__()
        html = (ROOT / "frontend/index.html").read_text()
        import re
        html = re.sub(r'<link[^>]*>', '', html)
        html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.S)
        self._page.set_content(html)
        self._page.add_style_tag(content=(ROOT / "frontend/dist/styles.css").read_text())
        self._page.add_script_tag(content=BRIDGE)
        self._page.add_script_tag(type="module", content=(ROOT / "frontend/dist/app.js").read_text())

    def route(self, pattern, handler):
        # Only used for the explicit XSS test. It does not intercept real network.
        holder = {}
        handler(SimpleNamespace(fulfill=lambda **kwargs: holder.update(kwargs)))
        self._routes[pattern.replace("**", "")] = holder["json"]

    def _request(self, request):
        for path, data in self._routes.items():
            if request["path"].endswith(path):
                return {"body": json.dumps(data), "status": 200, "headers": {"Content-Type": "application/json"}}
        kwargs = {"headers": request["headers"]}
        if request["files"]:
            kwargs["files"] = [(f["field"], (f["name"], base64.b64decode(f["data"]), f["mime"])) for f in request["files"]]
            kwargs["data"] = dict(request["fields"])
        elif request["body"] is not None:
            kwargs["content"] = request["body"]
        response = self._client.request(request["method"], request["path"], **kwargs)
        return {"body": response.text, "status": response.status_code, "headers": dict(response.headers)}

    def expect_download(self):
        return DownloadEvent(self._page)

    def close_harness(self):
        if self._client:
            self._client.__exit__(None, None, None)
