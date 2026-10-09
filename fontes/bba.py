"""Portal Itaú BBA Smart (itau.com.br/itaubba-pt/portal) via Playwright com perfil persistente — SÓ DOM.

SPA logada. Diferente do BTG, aqui NÃO se lê token nem se chama API interna: a coleta é só leitura do DOM
renderizado (os mesmos seletores de bba_chrome_lista.js / bba_chrome_resumo.js). Nada de page.route, cabeçalhos,
cookies ou localStorage.

    python bba_relatorios.py --login     # 1ª vez / sessão expirada: abre janela, VOCÊ faz login, fecha sozinho
    python bba_relatorios.py             # rotina headless: lista equity+macro, lê o resumo dos destaques

O perfil fica em data/bba/perfil (fora do git). Se a rotina cair em tela de login/verificação/captcha, ela avisa
uma linha no Telegram e para — nunca digita nada.
"""
from __future__ import annotations

import time

import config

PERFIL = config.DATA / "bba" / "perfil"
PORTAL = "https://www.itau.com.br/itaubba-pt/portal"
AREAS = {                                   # renda fixa (fi) ficou de fora: URL não confirmada / sem acesso
    "equity": PORTAL + "/equity?tab=reports",
    "macro": PORTAL + "/macro?tab=reports",
}
REPORT = PORTAL + "/{area}/report/{id}"


class PrecisaLogin(Exception):
    """A página pediu login/verificação/captcha: a rotina avisa e para, sem tentar nada."""


# Estado da página (só DOM): 'captcha' | 'ok' (lista de relatórios visível) | 'login' | 'vazio' (ainda carregando)
_JS_ESTADO = r"""() => {
  // lista visível vence: a página logada tem "reCAPTCHA" no rodapé, então o texto não serve de prova de captcha
  if (document.querySelector('a[href*="/report/"]')) return 'ok';
  const vis = el => { if (!el) return false; const r = el.getBoundingClientRect(); return r.width > 40 && r.height > 40; };
  const widget = [...document.querySelectorAll('iframe[src*="recaptcha"],iframe[src*="hcaptcha"],.g-recaptcha,#px-captcha,[id*="captcha" i]')].some(vis);
  if (widget) return 'captcha';
  if (document.querySelector('input[type=password]') || /\/(login|sso|auth|acesso)/i.test(location.pathname) || /id\.itau|sso|login/i.test(location.hostname)) return 'login';
  return 'vazio';
}"""

# Lista de uma área (adaptado de bba_chrome_lista.js). Args: [conhecidos[], dias, area].
_JS_LISTA = r"""([conhecidos, dias, area]) => {
  const CONHECIDOS = new Set(conhecidos);
  const MES = {jan:'01',fev:'02',mar:'03',abr:'04',mai:'05',jun:'06',jul:'07',ago:'08',set:'09',out:'10',nov:'11',dez:'12'};
  const lim = new Date(Date.now() - dias * 864e5).toISOString().slice(0, 10);
  const seen = new Set(), out = [];
  for (const a of document.querySelectorAll('a[href*="/report/"]')) {
    const id = (a.getAttribute('href').match(/report\/([0-9a-f-]{36})/) || [])[1];
    if (!id || seen.has(id)) continue;
    seen.add(id);
    if (CONHECIDOS.has(id)) continue;
    let c = a;
    for (let i = 0; i < 8 && c; i++) { c = c.parentElement; if (c && /\d{1,2} \w{3},? \d{4}/.test(c.innerText) && c.innerText.length < 900) break; }
    const txt = c ? c.innerText : '';
    const m = txt.match(/(\d{1,2}) (\w{3}),? (\d{4})/);
    const d = m ? `${m[3]}-${MES[m[2].toLowerCase()] || '01'}-${m[1].padStart(2, '0')}` : null;
    if (!d || d < lim) continue;
    const linhas = txt.split('\n').map(s => s.trim()).filter(s => s && !/_outline|_base|^\+\d+$|^e$/.test(s));
    let titulo = a.innerText.trim().replace(/\s+/g, ' ');
    let tipo = '';
    if (linhas[0] && linhas[0] !== titulo && titulo.startsWith(linhas[0])) { tipo = linhas[0]; titulo = titulo.slice(tipo.length).trim(); }
    else if (linhas[0] && linhas[0] !== titulo && !titulo.includes(linhas[0])) { tipo = linhas[0]; }
    const iData = linhas.findIndex(s => /\d{1,2} \w{3},? \d{4}/.test(s));
    const setores = linhas.slice(1, iData > 1 ? iData : 1).filter(s => !titulo.includes(s) && s !== tipo);
    const depois = linhas.slice(iData + 1);
    out.push({id, d, area, url: new URL(a.getAttribute('href'), location.href).pathname, tipo: tipo.slice(0, 40), t: titulo.slice(0, 220),
              co: (setores.join(' / ') || (depois.find(s => s.includes('•')) || '')).slice(0, 80),
              an: (depois.find(s => /^[A-ZÀ-Ú][a-zà-ú]+ [A-ZÀ-Ú]/.test(s) && !s.includes('•')) || '').slice(0, 40)});
  }
  return out;
}"""

# Resumo de um relatório (adaptado de bba_chrome_resumo.js). Devolve o texto já montado (cabeçalho + Resumo).
_JS_RESUMO = r"""() => {
  const id = (location.pathname.match(/report\/([0-9a-f-]{36})/) || [])[1];
  const body = document.body.innerText;
  const ini = body.indexOf('Resumo');
  const fimIdx = ['\nBest regards', '\nPlease refer to the relevant page', '\nCobertura\n', '\nCentral de Ajuda', '\nCompanies\n', 'Quem somos', 'Relatórios relacionados']
    .map(k => body.indexOf(k, ini + 6)).filter(i => i > 0);
  const fim = fimIdx.length ? Math.min(...fimIdx) : body.length;
  const cab = body.slice(0, ini > 0 ? ini : 600).split('\n').map(s => s.trim()).filter(s => s && !/_outline|_base|^(Home|Equity|Fixed Income|Macro|Eventos|R|L|e|Acesse o relatório)$/.test(s));
  const texto = (ini > 0 ? body.slice(ini + 6, fim) : body.slice(0, 8000)).replace(/\n(sort_up|copy_outline)\n/g, '\n').replace(/\n \n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
  const montado = [...new Set(cab)].slice(0, 6).join('\n') + '\n\nResumo\n' + texto;
  return {id, texto: montado, chars: texto.length};
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

    def confirmar_sessao(self, espera: int = 35) -> str:
        """Abre a lista de equity e espera a UI logada aparecer. Devolve 'ok' | 'login' | 'captcha'."""
        self.page.goto(AREAS["equity"], wait_until="domcontentloaded", timeout=90_000)
        fim = time.time() + espera
        st = "vazio"
        while time.time() < fim:
            st = self._estado()
            if st in ("ok", "login", "captcha"):
                return st
            time.sleep(2)
        return "login" if st == "vazio" else st     # não confirmou sessão => trata como login (seguro)

    def listar_area(self, area: str, conhecidos: list[str], dias: int) -> list[dict]:
        url = AREAS.get(area)
        if not url:
            return []
        self.page.goto(url, wait_until="domcontentloaded", timeout=90_000)
        try:
            self.page.wait_for_selector('a[href*="/report/"]', timeout=25_000)
        except Exception:
            st = self._estado()
            if st in ("login", "captcha"):
                raise PrecisaLogin(st)
            return []
        time.sleep(2)                               # deixa a lista terminar de hidratar
        return self.page.evaluate(_JS_LISTA, [list(conhecidos), dias, area])

    def resumo(self, area: str, id_: str, espera: int = 8) -> dict:
        """Lê o Resumo em HTML do relatório. Devolve {id, texto, chars}; chars 0 se não achou o resumo."""
        self.page.goto(REPORT.format(area=area, id=id_), wait_until="domcontentloaded", timeout=90_000)
        time.sleep(espera)
        try:
            return self.page.evaluate(_JS_RESUMO)
        except Exception as e:
            return {"id": id_, "texto": "", "chars": 0, "erro": str(e)[:120]}


def login_interativo(max_min: int = 120) -> bool:
    """Abre o portal numa janela visível e espera VOCÊ fazer login (até max_min minutos). O script nunca digita nada."""
    p = Portal(headless=False)
    try:
        p.page.goto(AREAS["equity"], wait_until="domcontentloaded", timeout=90_000)
        print(f"Faça login na janela do Itaú BBA que abriu. Aguardo até {max_min} min...", flush=True)
        fim = time.time() + max_min * 60
        while time.time() < fim:
            st = p._estado()
            if st == "ok":
                print(f"sessão confirmada; perfil salvo em {PERFIL}", flush=True)
                time.sleep(3)
                return True
            if st == "captcha":
                print("captcha na tela: resolva você mesmo; sigo aguardando a lista aparecer", flush=True)
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
