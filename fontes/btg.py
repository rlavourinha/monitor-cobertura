"""Portal de research do BTG Pactual (btgpactual.com/research) via Playwright com perfil persistente.

O portal é uma SPA autenticada: o token JWT fica no localStorage e o próprio app o renova (AwsAuth/RefreshToken).
A API interna (api.portal-research.btgpactual.com/research/...) é chamada de DENTRO da página (page.evaluate) com o
token que o app guarda, então o refresh continua por conta do portal. Endpoints usados (descobertos em 27/09/2026):

    Document/SimpleFilterImproved?department=ALL&number_of_rows=100&start_number_row=1   -> {selected_documents:[...]}
    document/reportDocument?report_id=<id>&email=<email>                                  -> {dataBytes: <PDF em base64>}
    user/user-info                                                                        -> {email, ...} (200 = logado)
    cabeçalhos: authorization: Bearer <accessToken>, culture-code: en_us, root-version: 30

Login: uma vez, com janela visível (`python btg_relatorios.py --login`); a sessão fica em .playwright/btg-profile
(fora do git). Quando expirar, o coletor avisa por Telegram e é só repetir o --login.
"""
from __future__ import annotations

import base64
import time

import config

PERFIL = config.RAIZ / ".playwright" / "btg-profile"
PORTAL = "https://www.btgpactual.com/research/"
LISTA = PORTAL + "latest-documents"
API = "https://api.portal-research.btgpactual.com/research/"

_JS_API = """async ([path, params]) => {
  const tok = localStorage.getItem('accessToken');
  const raw = tok && tok.startsWith('"') ? JSON.parse(tok) : tok;
  if (!raw) return {status: 0, dados: 'sem token no localStorage'};
  const u = new URL(path, 'https://api.portal-research.btgpactual.com/research/');
  for (const [k, v] of Object.entries(params || {})) u.searchParams.set(k, String(v));
  const r = await fetch(u, {headers: {accept: 'application/json', 'content-type': 'application/json',
                                      'culture-code': 'en_us', 'root-version': '30', authorization: 'Bearer ' + raw}});
  const ct = r.headers.get('content-type') || '';
  let dados = null;
  try { dados = ct.includes('json') ? await r.json() : await r.text(); } catch (e) { dados = null; }
  return {status: r.status, dados};
}"""


class Portal:
    """Contexto Playwright persistente apontado para o portal. Use como `with Portal() as p:`."""

    def __init__(self, headless: bool = True):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        PERFIL.mkdir(parents=True, exist_ok=True)
        opts = dict(headless=headless, viewport={"width": 1280, "height": 900}, locale="en-US",
                    args=["--disable-blink-features=AutomationControlled"])
        try:                                   # Chrome do sistema (impressão digital mais natural); senão o Chromium do Playwright
            self.ctx = self._pw.chromium.launch_persistent_context(str(PERFIL), channel="chrome", **opts)
        except Exception:
            self.ctx = self._pw.chromium.launch_persistent_context(str(PERFIL), **opts)
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        self.email: str | None = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.fechar()

    def fechar(self) -> None:
        try:
            self.ctx.close()
        finally:
            self._pw.stop()

    def api(self, path: str, params: dict | None = None) -> dict:
        """GET na API interna com o token do app. Devolve {status, dados}."""
        return self.page.evaluate(_JS_API, [path, params or {}])

    def logado(self) -> bool:
        try:
            r = self.api("user/user-info")
        except Exception:
            return False
        ok = r.get("status") == 200 and isinstance(r.get("dados"), dict) and bool(r["dados"].get("email"))
        if ok:
            self.email = r["dados"]["email"]
        return ok

    def abrir_lista(self, espera: int = 30) -> bool:
        """Carrega a página de últimos documentos (o app renova o token) e confirma que a sessão está válida."""
        self.page.goto(LISTA, wait_until="domcontentloaded", timeout=90_000)
        fim = time.time() + espera
        while time.time() < fim:
            if self.logado():
                return True
            time.sleep(2)
        return False

    def listar(self, n: int = 100, inicio: int = 1, departamento: str = "ALL") -> list[dict]:
        """Últimos relatórios (mais novo primeiro): publish_date, title, company_sector, main_analyst, rating, document_id, file_size (KB)."""
        r = self.api("Document/SimpleFilterImproved", {"department": departamento, "number_of_rows": n, "start_number_row": inicio})
        if r.get("status") != 200 or not isinstance(r.get("dados"), dict):
            raise RuntimeError(f"lista: HTTP {r.get('status')}")
        return r["dados"].get("selected_documents") or []

    def pdf(self, id_: int) -> bytes:
        if not self.email and not self.logado():
            raise RuntimeError("sem sessão")
        r = self.api("document/reportDocument", {"report_id": id_, "email": self.email})
        d = r.get("dados")
        if r.get("status") != 200 or not isinstance(d, dict) or not d.get("dataBytes"):
            raise RuntimeError(f"pdf {id_}: HTTP {r.get('status')} {str(d)[:120]}")
        return base64.b64decode(d["dataBytes"])


def login_interativo(max_min: int = 120) -> bool:
    """Abre o portal numa janela visível e espera o usuário fazer login (até max_min minutos)."""
    p = Portal(headless=False)
    try:
        p.page.goto(PORTAL, wait_until="domcontentloaded", timeout=90_000)
        print(f"Faça login na janela do Chrome que abriu. Aguardo até {max_min} min...", flush=True)
        fim = time.time() + max_min * 60
        while time.time() < fim:
            if p.logado():
                print(f"logado como {p.email}; sessão salva em {PERFIL}", flush=True)
                time.sleep(3)          # deixa o app terminar de gravar tokens
                return True
            time.sleep(3)
        print("tempo esgotado sem login", flush=True)
        return False
    except Exception as e:             # janela fechada pelo usuário etc.
        print(f"login interrompido: {e}", flush=True)
        return False
    finally:
        try:
            p.fechar()
        except Exception:
            pass
