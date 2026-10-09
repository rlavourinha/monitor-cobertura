"""Portal Bradesco BBI (BlueMatrix: bradesco-portal.bluematrix.com) via Playwright com perfil persistente — SÓ DOM.

SPA Vuetify. Acesso liberado por 365 dias no navegador depois da verificação por e-mail (feita UMA vez, por VOCÊ, na
janela do --login). A coleta é só leitura do DOM (mesmos seletores de bradesco_chrome_lista.js / bradesco_chrome_texto.js):
clica SEARCH / LIST / LOAD MORE e lê os cards; o texto integral vem da página do documento
(bradescobbi.bluematrix.com/links2/doc/html/<id>). Nada de page.route, cabeçalhos, cookies ou tokens.

    python bradesco_relatorios.py --login     # 1ª vez: abre janela, VOCÊ faz a verificação por e-mail, fecha sozinho
    python bradesco_relatorios.py             # rotina headless: lista a Busca Avançada, lê o texto dos destaques

Perfil em data/bradesco/perfil (fora do git). Tela de login/verificação/captcha => uma linha no Telegram e para.
"""
from __future__ import annotations

import time

import config

PERFIL = config.DATA / "bradesco" / "perfil"
PORTAL = "https://bradesco-portal.bluematrix.com"
BUSCA = PORTAL + "/advanced_search"
DOC = "https://bradescobbi.bluematrix.com/links2/doc/html/{id}"


class PrecisaLogin(Exception):
    """A página pediu login/verificação/captcha: a rotina avisa e para, sem tentar nada."""


# Estado da página só pelo DOM: 'captcha' | 'ok' (UI de busca visível => verificado) | 'login' | 'vazio'.
_JS_ESTADO = r"""() => {
  const t = document.body ? document.body.innerText : '';
  // UI de busca visível vence; captcha só conta com o widget de fato na tela (o texto da página pode citar reCAPTCHA)
  const temBusca = [...document.querySelectorAll('button')].some(b => ['SEARCH','LIST'].includes(b.innerText.trim())) || /Showing \d+ to/.test(t) || !!document.querySelector('a[href*="/report/"]');
  if (temBusca) return 'ok';
  // selo "protegido por reCAPTCHA" (anchor, 256x60) não é desafio; desafio = bframe/hcaptcha/px visível e grande
  const vis = el => { if (!el) return false; const r = el.getBoundingClientRect(); return r.width > 150 && r.height > 150; };
  if ([...document.querySelectorAll('iframe[src*="recaptcha/api2/bframe"],iframe[src*="hcaptcha.com"],#px-captcha,[id*="captcha-challenge" i]')].some(vis)) return 'captcha';
  if (document.querySelector('input[type=email],input[type=text]') && /verif|código|code|e-?mail|access code|enter the/i.test(t)) return 'login';
  return 'vazio';
}"""

# Lista da Busca Avançada (adaptado de bradesco_chrome_lista.js). Clica SEARCH/LIST/LOAD MORE e lê os cards.
# Args: [paginas]. Devolve {status, todos:[{id,d,an,co,t,sin,url}]}.
_JS_LISTA = r"""async ([paginas]) => {
  const esperar = ms => new Promise(r => setTimeout(r, ms));
  const botao = t => [...document.querySelectorAll('button')].find(b => b.innerText.trim() === t);
  const t0 = document.body.innerText;
  const visC = el => { const r = el.getBoundingClientRect(); return r.width > 40 && r.height > 40; };
  if ([...document.querySelectorAll('iframe[src*="recaptcha"],iframe[src*="hcaptcha"]')].some(visC)) return {status: 'captcha', todos: []};
  if (!/Showing \d+ to/.test(t0)) { const s = botao('SEARCH'); if (s) { s.click(); await esperar(6000); } }
  if (!document.querySelector('a[href*="/report/"]')) { const l = botao('LIST'); if (l) { l.click(); await esperar(5000); } }
  for (let i = 1; i < paginas; i++) { const m = botao('LOAD MORE'); if (!m) break; m.click(); await esperar(5000); }
  const temLista = /Showing \d+ to/.test(document.body.innerText) || !!document.querySelector('a[href*="/report/"]');
  if (!temLista) {
    const b = document.body.innerText;
    if (document.querySelector('input[type=email],input[type=text]') && /verif|código|code|e-?mail|access code|enter the/i.test(b)) return {status: 'login', todos: []};
    return {status: 'login', todos: []};
  }
  const MES = {Jan:'01',Feb:'02',Mar:'03',Apr:'04',May:'05',Jun:'06',Jul:'07',Aug:'08',Sep:'09',Oct:'10',Nov:'11',Dec:'12'};
  const seen = new Set(), todos = [];
  for (const a of document.querySelectorAll('a[href*="/report/"]')) {
    const id = (a.getAttribute('href').match(/report\/([0-9a-f-]{36})/) || [])[1];
    if (!id || seen.has(id)) continue;
    seen.add(id);
    const L = a.innerText.split('\n').map(s => s.trim()).filter(Boolean);
    const iD = L.findIndex(s => /^[A-Z][a-z]{2} \d{1,2}, \d{4}/.test(s));
    const m = iD >= 0 ? L[iD].match(/^([A-Z][a-z]{2}) (\d{1,2}), (\d{4}), (\d{1,2}):(\d{2}) (AM|PM)/) : null;
    const hh = m ? (+m[4] % 12) + (m[6] === 'PM' ? 12 : 0) : 0;
    const d = m ? `${m[3]}-${MES[m[1]]}-${m[2].padStart(2, '0')} ${String(hh).padStart(2, '0')}:${m[5]}` : null;
    todos.push({id, d, an: iD > 0 ? L[0] : '', co: (L[iD + 1] || '').slice(0, 80), t: (L[iD + 2] || '').slice(0, 220),
                sin: (L.slice(iD + 3).join(' ') || '').replace(/^No Synopsis Available$/, '').slice(0, 300), url: '/report/' + id});
  }
  return {status: 'ok', todos};
}"""

# Texto integral de um relatório (adaptado de bradesco_chrome_texto.js). Devolve {id, texto, chars}.
_JS_TEXTO = r"""() => {
  const id = (location.pathname.match(/([0-9a-f-]{36})/) || [])[1];
  let body = document.body.innerText.replace(/\r/g, '');
  if (/access code|enter the|verifique seu e-?mail|sign in|log in/i.test(body) && body.length < 600) return {id, texto: '', chars: 0, login: true};
  body = body.replace(/^\s*REPORT CONTENT\s*DISCLOSURES\s*VIEW PDF\s*/i, '');
  const fimIdx = ['\nANALYST CERTIFICATION', '\nAnalyst Certification', '\nIMPORTANT DISCLOSURES', '\nImportant Disclosures', '\nDISCLOSURES\n', '\nDisclosures\n', '\nRating Distribution', '\nBradesco S.A. Corretora de Títulos', '\nExpand All Disclosures']
    .map(k => body.indexOf(k, 300)).filter(i => i > 0);
  const fim = fimIdx.length ? Math.min(...fimIdx) : body.length;
  const texto = body.slice(0, fim).replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim().slice(0, 16000);
  return {id, texto, chars: texto.length};
}"""


class Portal:
    """Contexto Playwright persistente apontado para o portal. Use como `with Portal() as p:`."""

    def __init__(self, headless: bool = True):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        PERFIL.mkdir(parents=True, exist_ok=True)
        opts = dict(headless=headless, viewport={"width": 1366, "height": 950}, locale="pt-BR",
                    args=["--disable-blink-features=AutomationControlled"])
        try:
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

    def _estado(self) -> str:
        try:
            return self.page.evaluate(_JS_ESTADO)
        except Exception:
            return "vazio"

    def coletar_lista(self, paginas: int = 2, espera_carga: int = 8) -> list[dict]:
        """Abre a Busca Avançada e lê os cards. Levanta PrecisaLogin se cair em verificação/captcha."""
        self.page.goto(BUSCA, wait_until="domcontentloaded", timeout=90_000)
        time.sleep(espera_carga)                    # a SPA Vuetify demora a montar
        r = self.page.evaluate(_JS_LISTA, [paginas])
        if r.get("status") != "ok":
            raise PrecisaLogin(r.get("status") or "login")
        return r.get("todos") or []

    def texto(self, id_: str, espera: int = 6) -> dict:
        """Lê o texto integral do documento. Devolve {id, texto, chars}; chars 0 se bloqueou/vazio."""
        self.page.goto(DOC.format(id=id_), wait_until="domcontentloaded", timeout=90_000)
        time.sleep(espera)
        try:
            return self.page.evaluate(_JS_TEXTO)
        except Exception as e:
            return {"id": id_, "texto": "", "chars": 0, "erro": str(e)[:120]}


def login_interativo(max_min: int = 120) -> bool:
    """Abre a Busca Avançada numa janela visível e espera VOCÊ fazer a verificação por e-mail. Nunca digita nada."""
    p = Portal(headless=False)
    try:
        p.page.goto(BUSCA, wait_until="domcontentloaded", timeout=90_000)
        print(f"Faça a verificação por e-mail na janela do Bradesco BBI que abriu. Aguardo até {max_min} min...", flush=True)
        fim = time.time() + max_min * 60
        while time.time() < fim:
            st = p._estado()
            if st == "ok":
                print(f"sessão confirmada; perfil salvo em {PERFIL}", flush=True)
                time.sleep(3)
                return True
            if st == "captcha":
                print("captcha na tela: resolva você mesmo; sigo aguardando a busca aparecer", flush=True)
            time.sleep(3)
        print("tempo esgotado sem confirmar a sessão", flush=True)
        return False
    except Exception as e:
        print(f"login interrompido: {e}", flush=True)
        return False
    finally:
        try:
            p.fechar()
        except Exception:
            pass
