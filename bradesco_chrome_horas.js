// Bradesco BBI — hora de publicação (modo Chrome, javascript_tool). Rodar em https://bradesco-portal.bluematrix.com/
// (Home) ANTES de bradesco_chrome_lista.js. A Busca Avançada só mostra a hora dos relatórios de hoje ("3 hours ago");
// dias anteriores vêm como "Thu 26 Nov 2026". A Home (notas de analista; os diários não aparecem nela) mostra
// "Nov 26, 2026, 10:06 PM". Guarda {id: "AAAA-MM-DD HH:MM"} em localStorage['bdx_horas'] (mesma origem da Busca
// Avançada), que a lista usa para completar a hora. Pedido de 27/11/2026: saber se o relatório saiu de manhã ou à noite.
const esperar = ms => new Promise(r => setTimeout(r, ms));
const botao = t => [...document.querySelectorAll('button')].find(b => b.innerText.trim().toUpperCase() === t);
for (let i = 0; i < 2; i++) { const m = botao('LOAD MORE'); if (!m) break; m.click(); await esperar(4000); }
const MES = {Jan: '01', Feb: '02', Mar: '03', Apr: '04', May: '05', Jun: '06', Jul: '07', Aug: '08', Sep: '09', Oct: '10', Nov: '11', Dec: '12'};
const horas = {};
for (const a of document.querySelectorAll('a[href*="/report/"]')) {
  const id = (a.getAttribute('href').match(/report\/([0-9a-f-]{36})/) || [])[1];
  const m = a.innerText.match(/([A-Z][a-z]{2}) (\d{1,2}), (\d{4}), (\d{1,2}):(\d{2}) (AM|PM)/);
  if (!id || !m || !MES[m[1]]) continue;
  const h = (+m[4] % 12) + (m[6] === 'PM' ? 12 : 0);
  horas[id] = `${m[3]}-${MES[m[1]]}-${m[2].padStart(2, '0')} ${String(h).padStart(2, '0')}:${m[5]}`;
}
localStorage.setItem('bdx_horas', JSON.stringify(horas));
JSON.stringify({horas: Object.keys(horas).length});
