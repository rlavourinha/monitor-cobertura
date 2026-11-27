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
// acesso caído: o portal manda para sso.bluematrix.com ("BlueMatrix - Verification", re-verificação por e-mail).
// Sai com erro explícito, para a rotina avisar em vez de responder "nada novo". De 25 a 27/11/2026 a rotina respondeu
// "nada novo" porque o portal mudou o formato da data e nenhum card era reconhecido (daí DATA_ILEGIVEL abaixo).
const caiu = () => /sso\.bluematrix/.test(location.host) || /Verification/i.test(document.title) ||
  /re-verify your email/i.test(document.body.innerText);
if (caiu()) throw new Error('ACESSO_EXPIRADO: portal pede verificação por e-mail');
const botao = t => [...document.querySelectorAll('button')].find(b => b.innerText.trim() === t);
if (!/Showing \d+ to/.test(document.body.innerText)) { const s = botao('SEARCH'); if (s) { s.click(); await esperar(6000); } }
if (!document.querySelector('a[href*="/report/"]')) { const l = botao('LIST'); if (l) { l.click(); await esperar(5000); } }
for (let i = 1; i < PAGINAS; i++) { const m = botao('LOAD MORE'); if (!m) break; m.click(); await esperar(5000); }
// Data do card. Formatos vistos: "Nov 26, 2026, 3:12 PM" (até ~24/11/2026) e, depois da mudança do portal,
// "24 minutes ago" / "3 hours ago" (hoje) e "Thu 26 Nov 2026" (dias anteriores, sem hora -> 00:00). Hora local.
const MES = {Jan: '01', Feb: '02', Mar: '03', Apr: '04', May: '05', Jun: '06', Jul: '07', Aug: '08', Sep: '09', Oct: '10', Nov: '11', Dec: '12'};
const pad = n => String(n).padStart(2, '0');
const fmt = t => `${t.getFullYear()}-${pad(t.getMonth() + 1)}-${pad(t.getDate())} ${pad(t.getHours())}:${pad(t.getMinutes())}`;
const h12 = (h, ap) => (+h % 12) + (/PM/i.test(ap || '') ? 12 : 0);
const lerData = s => {
  let m = s.match(/^([A-Z][a-z]{2}) (\d{1,2}), (\d{4}), (\d{1,2}):(\d{2}) (AM|PM)/);
  if (m && MES[m[1]]) return `${m[3]}-${MES[m[1]]}-${pad(m[2])} ${pad(h12(m[4], m[6]))}:${m[5]}`;
  if (/^(just now|now|a few seconds ago)$/i.test(s)) return fmt(new Date());
  m = s.match(/^(an?|one|\d+) (second|minute|hour|day)s? ago$/i);
  if (m) { const n = /^\d+$/.test(m[1]) ? +m[1] : 1; const u = {second: 1e3, minute: 6e4, hour: 36e5, day: 864e5}[m[2].toLowerCase()];
           return fmt(new Date(Date.now() - n * u)); }
  m = s.match(/^(today|yesterday)$/i);
  if (m) { const t = new Date(); if (/yesterday/i.test(m[1])) t.setDate(t.getDate() - 1); t.setHours(0, 0, 0, 0); return fmt(t); }
  m = s.match(/^(?:[A-Z][a-z]{2},? )?(\d{1,2}) ([A-Z][a-z]{2}),? (\d{4})(?:,? (\d{1,2}):(\d{2})\s*(AM|PM)?)?$/);
  if (m && MES[m[2]]) return `${m[3]}-${MES[m[2]]}-${pad(m[1])} ${m[4] ? pad(h12(m[4], m[6])) + ':' + m[5] : '00:00'}`;
  return null;
};
const _l = new Date(Date.now() - DIAS * 864e5);
const lim = `${_l.getFullYear()}-${pad(_l.getMonth() + 1)}-${pad(_l.getDate())}`;
const seen = new Set(), todos = [];
for (const a of document.querySelectorAll('a[href*="/report/"]')) {
  const id = (a.getAttribute('href').match(/report\/([0-9a-f-]{36})/) || [])[1];
  if (!id || seen.has(id)) continue;
  seen.add(id);
  const L = a.innerText.split('\n').map(s => s.trim()).filter(Boolean);
  const iD = L.findIndex(s => lerData(s));
  const d = iD >= 0 ? lerData(L[iD]) : null;
  todos.push({id, d, an: iD > 0 ? L[0] : '', co: (L[iD + 1] || '').slice(0, 80), t: (L[iD + 2] || '').slice(0, 220),
              sin: (L.slice(iD + 3).join(' ') || '').replace(/^No Synopsis Available$/, '').slice(0, 300), url: '/report/' + id});
}
if (caiu()) throw new Error('ACESSO_EXPIRADO: portal pede verificação por e-mail');
if (!todos.length) throw new Error('LISTA_VAZIA: a Busca Avançada não devolveu nenhum card');
const semData = todos.filter(o => !o.d).length;
if (semData > todos.length / 2) throw new Error(`DATA_ILEGIVEL: ${semData} de ${todos.length} cards sem data reconhecida`);
sessionStorage.setItem('bdx_lista', JSON.stringify(todos));
const novos = todos.filter(o => o.d && o.d.slice(0, 10) >= lim && !CONHECIDOS.has(o.id));
sessionStorage.setItem('bdx_novos', JSON.stringify(novos));
JSON.stringify({lidos: todos.length, mais_antigo: todos.map(o => o.d).filter(Boolean).sort()[0], novos: novos.length, ids: novos.map(o => o.id.slice(0, 8) + ' ' + (o.d || '').slice(5, 16) + ' ' + o.t.slice(0, 40))});
