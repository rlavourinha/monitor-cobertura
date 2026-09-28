// Modo Chrome do coletor Smart — PASSO 0 (mapeamento da API). Roda via javascript_tool da extensão Claude in Chrome numa
// aba do Itaú BBA Smart já logada (qualquer máquina com Chrome + extensão; não precisa ser o laptop).
// Instala um interceptador de fetch/XHR. Depois: (1) navegue na lista de relatórios (troque de página/filtro para forçar
// a chamada), (2) abra um relatório até o PDF aparecer, (3) execute `window.__smartMapa()` e leia o resultado:
// URL/método/cabeçalhos de cada chamada, a forma do JSON de resposta (chaves e tipos), onde a SPA guarda o token e os
// cookies. Nada sai da página; valores de Authorization/token são truncados. Com isso preencha MAPA e CAMPOS em
// fontes/smart.py e escreva smart_chrome_lista.js / smart_chrome_extrai.js nos moldes dos btg_chrome_*.js.
(() => {
  if (window.__smartCap) return 'interceptador já instalado: rode window.__smartMapa()';
  const cap = window.__smartCap = [];
  const resumo = (v, prof = 0) => {                    // forma do JSON: chaves e tipos, sem valores longos
    if (Array.isArray(v)) return v.length ? ['[' + v.length + ']', resumo(v[0], prof + 1)] : '[]';
    if (v && typeof v === 'object') { if (prof > 3) return '{…}'; const o = {}; for (const k of Object.keys(v).slice(0, 40)) o[k] = resumo(v[k], prof + 1); return o; }
    if (typeof v === 'string') return v.length > 60 ? v.slice(0, 57) + '…' : v;
    return v;
  };
  const oculta = (k, v) => /auth|token|key|cookie/i.test(k) ? String(v).slice(0, 12) + '…' : String(v);
  const registra = (metodo, url, cabecalhos, status, ct, corpoJson, tamanho) => {
    let resposta;
    try { resposta = /json/.test(ct) ? resumo(corpoJson()) : ct + ' ' + (tamanho || ''); } catch (e) { resposta = 'erro ' + e; }
    cap.push({t: new Date().toISOString().slice(11, 19), metodo, url: String(url), status, cabecalhos, resposta});
  };
  const F = window.fetch;
  window.fetch = async function (u, o) {
    const r = await F.apply(this, arguments);
    try {
      const h = {}; new Headers((o && o.headers) || (u instanceof Request ? u.headers : {})).forEach((v, k) => h[k] = oculta(k, v));
      const ct = r.headers.get('content-type') || '';
      const c = r.clone();
      if (/json/.test(ct)) c.json().then(j => registra((o && o.method) || 'GET', u instanceof Request ? u.url : u, h, r.status, ct, () => j)).catch(() => {});
      else registra((o && o.method) || 'GET', u instanceof Request ? u.url : u, h, r.status, ct, null, r.headers.get('content-length'));
    } catch (e) {}
    return r;
  };
  const XO = XMLHttpRequest.prototype.open, XS = XMLHttpRequest.prototype.setRequestHeader, XE = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (m, u) { this.__m = m; this.__u = u; this.__h = {}; return XO.apply(this, arguments); };
  XMLHttpRequest.prototype.setRequestHeader = function (k, v) { this.__h[k] = oculta(k, v); return XS.apply(this, arguments); };
  XMLHttpRequest.prototype.send = function () {
    this.addEventListener('loadend', () => {
      const ct = this.getResponseHeader('content-type') || '';
      registra(this.__m, this.__u, this.__h, this.status, ct, () => JSON.parse(this.responseText), this.getResponseHeader('content-length'));
    });
    return XE.apply(this, arguments);
  };
  window.__smartMapa = () => {
    const armazem = {};
    for (const [nome, s] of [['localStorage', localStorage], ['sessionStorage', sessionStorage]])
      for (let i = 0; i < s.length; i++) { const k = s.key(i), v = s.getItem(k) || ''; armazem[nome + '.' + k] = v.length + ' chars' + (/^"?eyJ/.test(v) ? ' (JWT)' : ''); }
    const cookies = document.cookie.split(';').map(c => c.trim().split('=')[0]).filter(Boolean);
    const chamadas = cap.filter(c => !/\.(js|css|map|png|jpg|svg|woff2?|ico)(\?|$)/i.test(c.url));
    return {pagina: location.href, chamadas, armazem, cookies};
  };
  return 'interceptador instalado: navegue na lista, abra um relatório e rode window.__smartMapa()';
})();
