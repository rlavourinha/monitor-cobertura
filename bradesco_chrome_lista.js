// Bradesco BBI (portal BlueMatrix) — lista de relatórios (modo Chrome, javascript_tool da extensão). Rodar em
//   https://bradesco-portal.bluematrix.com/advanced_search   (ordenação padrão: Publish Date, mais recente primeiro)
// A visão CARD renderiza vazia na extensão; a visão LIST mostra 15 itens por página e "LOAD MORE" traz mais 15.
// Só lê o DOM: clica SEARCH (se a lista ainda não apareceu), LIST e LOAD MORE (PAGINAS-1 vezes) e lê os cards.
// Devolve só os relatórios dos últimos DIAS dias que não estão em CONHECIDOS; grava a lista completa em
// sessionStorage['bdx_lista'] (para copiar com bradesco_chrome_copia_lista.js quando a saída for longa).
// Substituir CONHECIDOS, DIAS e PAGINAS antes de executar.
const CONHECIDOS = new Set([]);
const DIAS = 3;
const PAGINAS = 2;
const esperar = ms => new Promise(r => setTimeout(r, ms));
const botao = t => [...document.querySelectorAll('button')].find(b => b.innerText.trim() === t);
if (!/Showing \d+ to/.test(document.body.innerText)) { const s = botao('SEARCH'); if (s) { s.click(); await esperar(6000); } }
if (!document.querySelector('a[href*="/report/"]')) { const l = botao('LIST'); if (l) { l.click(); await esperar(5000); } }
for (let i = 1; i < PAGINAS; i++) { const m = botao('LOAD MORE'); if (!m) break; m.click(); await esperar(5000); }
const MES = {Jan: '01', Feb: '02', Mar: '03', Apr: '04', May: '05', Jun: '06', Jul: '07', Aug: '08', Sep: '09', Oct: '10', Nov: '11', Dec: '12'};
const lim = new Date(Date.now() - DIAS * 864e5).toISOString().slice(0, 10);
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
sessionStorage.setItem('bdx_lista', JSON.stringify(todos));
const novos = todos.filter(o => o.d && o.d.slice(0, 10) >= lim && !CONHECIDOS.has(o.id));
sessionStorage.setItem('bdx_novos', JSON.stringify(novos));
JSON.stringify({lidos: todos.length, novos: novos.length, ids: novos.map(o => o.id.slice(0, 8) + ' ' + (o.d || '').slice(5, 16) + ' ' + o.t.slice(0, 40))});
