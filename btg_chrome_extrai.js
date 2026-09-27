// Modo Chrome do coletor BTG — PASSO A (extração). Roda via javascript_tool da extensão Claude in Chrome numa aba do
// portal em /research/report-view/<qualquer id> com o visor carregado (esperar ~7 s após navegar).
// Baixa os PDFs dos IDS pela API interna, extrai o texto com o pdf.js do visor e guarda em window.__blocos
// (blocos "<<<BTG id>>>\ntexto"). Nada sai da página neste passo. Substituir IDS antes de executar.
// Devolve {id: {paginas, chars}} ou {id: {erro}}. Depois: btg_chrome_copia.js (passo B) e `python btg_relatorios.py --clip`.
const IDS = [];
const EMAIL = 'rlavourinha@petros.com.br';
const MAXPAG = 40;
const fr = [...document.querySelectorAll('iframe')].find(f => { try { return !!f.contentWindow.PDFViewerApplication; } catch (e) { return false; } });
if (!fr) throw new Error('visor pdf.js não encontrado: navegue para um /research/report-view/<id> e espere 7 s');
const pdfjs = fr.contentWindow.pdfjsLib;
const tok = localStorage.getItem('accessToken'); const raw = tok && tok.startsWith('"') ? JSON.parse(tok) : tok;
const H = {accept: 'application/json', 'content-type': 'application/json', 'culture-code': 'en_us', 'root-version': '30', authorization: 'Bearer ' + raw};
const out = {}; window.__blocos = [];
for (const id of IDS) {
  try {
    const r = await fetch(`https://api.portal-research.btgpactual.com/research/document/reportDocument?report_id=${id}&email=${EMAIL}`, {headers: H});
    const j = await r.json(); if (!j.dataBytes) throw new Error('sem dataBytes HTTP ' + r.status);
    const bin = atob(j.dataBytes); const arr = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
    const doc = await pdfjs.getDocument({data: arr}).promise; const parts = [];
    for (let p = 1; p <= Math.min(doc.numPages, MAXPAG); p++) {
      const tc = await (await doc.getPage(p)).getTextContent(); let line = '', y = null, txt = '';
      for (const it of tc.items) { if (y !== null && Math.abs(it.transform[5] - y) > 2) { txt += line.trim() + '\n'; line = ''; } line += it.str + (it.hasEOL ? '\n' : ' '); y = it.transform[5]; }
      txt += line.trim(); parts.push('[página ' + p + ']\n' + txt.replace(/[ \t]+\n/g, '\n').replace(/ {2,}/g, ' '));
    }
    const texto = parts.join('\n\n'); window.__blocos.push('<<<BTG ' + id + '>>>\n' + texto); out[id] = {paginas: doc.numPages, chars: texto.length};
  } catch (e) { out[id] = {erro: String(e).slice(0, 160)}; }
}
out;
