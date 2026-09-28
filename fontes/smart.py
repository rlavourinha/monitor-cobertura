"""Portal Itaú BBA Smart (itau.com.br/itaubba-pt/portal) via Playwright com perfil persistente. Mesma interface de fontes/btg.py.

O Smart é o portal de research do Itaú BBA (equity, renda fixa, macro): login com e-mail e senha, conteúdo liberado por
perfil. A SPA chama uma API interna que só aparece com a sessão aberta, e o portal é bloqueado fora do laptop — por isso
tudo que depende dela fica isolado em MAPA e CAMPOS, para preencher UMA vez, na aba logada (DevTools ou Claude in Chrome):

    1. Network → Fetch/XHR: abra a lista de relatórios e anote a requisição que devolve o JSON da lista — URL base,
       caminho, parâmetros de paginação e cabeçalhos (Authorization? cookie? culture/idioma?).
    2. Abra um relatório e anote a requisição que devolve o PDF: URL com o id e se vem em JSON (base64) ou binário.
    3. Application → Local/Session Storage/Cookies: onde a SPA guarda o token; e um endpoint barato que responde 200 só
       logado (perfil do usuário).
    4. Preencha MAPA e CAMPOS, rode `python smart_relatorios.py --login` e depois
       `python smart_relatorios.py --headed --dias 2 --sem-telegram --sem-espelho`.

Enquanto MAPA estiver incompleto, listar()/pdf() param com uma mensagem clara em vez de adivinhar.
Login: uma vez, com janela visível (`python smart_relatorios.py --login`); a sessão fica em .playwright/smart-profile
(fora do git). Quando expirar, o coletor avisa por Telegram e é só repetir o --login.
"""
from __future__ import annotations

import base64
import time
from urllib.parse import urljoin

import config

PERFIL = config.RAIZ / ".playwright" / "smart-profile"
PORTAL = "https://www.itau.com.br/itaubba-pt/portal/"
LISTA = PORTAL

# Pontos que dependem da API interna do Smart. None = ainda não mapeado.
MAPA: dict = {
    "api": None,            # base da API interna, ex.: "https://api.smart.itaubba.com.br/"
    "lista": None,          # caminho da lista; {n} e {inicio} são substituídos, ex.: "documents?size={n}&page={inicio}"
    "lista_itens": None,    # chave do array na resposta (a.b.c aceito); None = a resposta já é a lista
    "pdf": None,            # caminho do PDF com {id}, ex.: "documents/{id}/file"
    "pdf_base64": None,     # chave do base64 no JSON de resposta; None = a resposta é o PDF binário
    "logado": None,         # endpoint que responde 200 só com sessão válida, ex.: "users/me"
    "token": None,          # chave do localStorage/sessionStorage com o bearer; None = a sessão vai só por cookies
    "cabecalhos": {},       # cabeçalhos fixos que a SPA manda em toda chamada (idioma, versão, canal...)
}
# Nome do campo no JSON da lista para cada metadado do coletor (a.b.c aceito). rating e kb podem ficar None.
CAMPOS: dict = {"id": None, "data": None, "empresa": None, "titulo": None, "analista": None, "rating": None, "kb": None}

_JS_API = """async ([url, cabecalhos, tokenChave]) => {
  const h = Object.assign({accept: 'application/json, application/pdf, */*'}, cabecalhos || {});
  if (tokenChave) {
    const tok = localStorage.getItem(tokenChave) || sessionStorage.getItem(tokenChave);
    const raw = tok && tok.startsWith('"') ? JSON.parse(tok) : tok;
    if (!raw) return {status: 0, dados: 'sem token em ' + tokenChave};
    h.authorization = 'Bearer ' + raw;
  }
  const r = await fetch(url, {headers: h, credentials: 'include'});
  const ct = r.headers.get('content-type') || '';
  let dados = null;
  try {
    if (ct.includes('json')) dados = await r.json();
    else if (ct.includes('pdf') || ct.includes('octet-stream')) {
      const b = new Uint8Array(await r.arrayBuffer()); let s = '';
      for (let i = 0; i < b.length; i += 0x8000) s += String.fromCharCode.apply(null, b.subarray(i, i + 0x8000));
      dados = {_pdf_base64: btoa(s)};
    } else dados = await r.text();
  } catch (e) { dados = null; }
  return {status: r.status, dados};
}"""


def _exige(*chaves: str) -> None:
    faltam = [k for k in chaves if not MAPA.get(k)]
    if faltam:
        raise RuntimeError(f"Smart: ainda não mapeado em fontes/smart.py MAPA: {', '.join(faltam)} (ver docstring)")


def _pega(obj, caminho: str | None):
    """obj["a"]["b"] para caminho "a.b"; None se faltar."""
    if not caminho:
        return obj
    for parte in caminho.split("."):
        if not isinstance(obj, dict):
            return None
        obj = obj.get(parte)
    return obj


def normalizar(item: dict) -> dict:
    """Item cru da lista -> {id, data, empresa, titulo, analista, rating, kb} pelos nomes em CAMPOS."""
    if not all(CAMPOS.get(k) for k in ("id", "data", "empresa", "titulo", "analista")):
        raise RuntimeError("Smart: CAMPOS incompleto em fontes/smart.py (id, data, empresa, titulo, analista)")
    kb = _pega(item, CAMPOS["kb"]) if CAMPOS.get("kb") else 0
    return {"id": _pega(item, CAMPOS["id"]), "data": str(_pega(item, CAMPOS["data"]) or "")[:10],
            "empresa": _pega(item, CAMPOS["empresa"]) or "", "titulo": _pega(item, CAMPOS["titulo"]) or "",
            "analista": _pega(item, CAMPOS["analista"]) or "",
            "rating": (_pega(item, CAMPOS["rating"]) if CAMPOS.get("rating") else "") or "", "kb": int(kb or 0)}


class Portal:
    """Contexto Playwright persistente apontado para o Smart. Use como `with Portal() as p:`."""

    def __init__(self, headless: bool = True):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        PERFIL.mkdir(parents=True, exist_ok=True)
        opts = dict(headless=headless, viewport={"width": 1280, "height": 900}, locale="pt-BR",
                    args=["--disable-blink-features=AutomationControlled"])
        try:                                   # Chrome do sistema; senão o Chromium do Playwright
            self.ctx = self._pw.chromium.launch_persistent_context(str(PERFIL), channel="chrome", **opts)
        except Exception:
            self.ctx = self._pw.chromium.launch_persistent_context(str(PERFIL), **opts)
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.fechar()

    def fechar(self) -> None:
        try:
            self.ctx.close()
        finally:
            self._pw.stop()

    def api(self, path: str) -> dict:
        """GET na API interna de dentro da página (cookies da sessão + bearer, se MAPA['token']). Devolve {status, dados}."""
        _exige("api")
        return self.page.evaluate(_JS_API, [urljoin(MAPA["api"], path), MAPA.get("cabecalhos") or {}, MAPA.get("token")])

    def logado(self) -> bool:
        if MAPA.get("logado") and MAPA.get("api"):
            try:
                return self.api(MAPA["logado"]).get("status") == 200
            except Exception:
                return False
        # Sem endpoint mapeado: heurística (URL do portal sem 'login' e sem tela de senha visível).
        try:
            url = self.page.url.lower()
            senha = self.page.locator("input[type=password]").count()
            return url.startswith(PORTAL.lower()) and "login" not in url and senha == 0
        except Exception:
            return False

    def abrir_lista(self, espera: int = 30) -> bool:
        """Carrega o portal (a SPA renova a sessão) e confirma que está logado."""
        self.page.goto(LISTA, wait_until="domcontentloaded", timeout=90_000)
        fim = time.time() + espera
        while time.time() < fim:
            if self.logado():
                return True
            time.sleep(2)
        return False

    def listar(self, n: int = 100, inicio: int = 1) -> list[dict]:
        """Últimos relatórios já normalizados: {id, data, empresa, titulo, analista, rating, kb}."""
        _exige("api", "lista")
        r = self.api(MAPA["lista"].format(n=n, inicio=inicio))
        itens = _pega(r.get("dados"), MAPA.get("lista_itens"))
        if r.get("status") != 200 or not isinstance(itens, list):
            raise RuntimeError(f"lista: HTTP {r.get('status')} {str(r.get('dados'))[:120]}")
        return [normalizar(d) for d in itens]

    def pdf(self, id_) -> bytes:
        _exige("api", "pdf")
        r = self.api(MAPA["pdf"].format(id=id_))
        d = r.get("dados")
        b64 = None
        if isinstance(d, dict):
            b64 = d.get("_pdf_base64") or (_pega(d, MAPA["pdf_base64"]) if MAPA.get("pdf_base64") else None)
        if r.get("status") != 200 or not b64:
            raise RuntimeError(f"pdf {id_}: HTTP {r.get('status')} {str(d)[:120]}")
        return base64.b64decode(b64)


def login_interativo(max_min: int = 120) -> bool:
    """Abre o Smart numa janela visível e espera o usuário fazer login (até max_min minutos)."""
    p = Portal(headless=False)
    try:
        p.page.goto(PORTAL, wait_until="domcontentloaded", timeout=90_000)
        print(f"Faça login na janela do Chrome que abriu. Aguardo até {max_min} min...", flush=True)
        if not MAPA.get("logado"):
            print("(MAPA['logado'] vazio: detecção de login por heurística — feche a janela quando terminar)", flush=True)
        fim = time.time() + max_min * 60
        while time.time() < fim:
            if p.logado():
                print(f"logado; sessão salva em {PERFIL}", flush=True)
                time.sleep(3)          # deixa a SPA terminar de gravar tokens
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
