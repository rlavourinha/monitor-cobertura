// Itaú BBA Smart — lista de relatórios (modo Chrome, javascript_tool da extensão). Rodar numa página de lista:
//   https://www.itau.com.br/itaubba-pt/portal/equity?tab=reports   (AREA = 'equity')
//   https://www.itau.com.br/itaubba-pt/portal/macro?tab=reports    (AREA = 'macro')
//   (renda fixa: ver bba_relatorios.py / memória para a URL)        (AREA = 'fi')
// Só lê o DOM (cards). Devolve os relatórios dos últimos DIAS dias que não estão em CONHECIDOS.
// Substituir CONHECIDOS, DIAS e AREA antes de executar.
const CONHECIDOS = new Set([]);
const DIAS = 3;
const AREA = 'equity';
const MES = {jan: '01', fev: '02', mar: '03', abr: '04', mai: '05', jun: '06', jul: '07', ago: '08', set: '09', out: '10', nov: '11', dez: '12'};
const lim = new Date(Date.now() - DIAS * 864e5).toISOString().slice(0, 10);
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
  out.push({id, d, area: AREA, url: new URL(a.getAttribute('href'), location.href).pathname, tipo: tipo.slice(0, 40), t: titulo.slice(0, 220),
            co: (setores.join(' / ') || (depois.find(s => s.includes('•')) || '')).slice(0, 80),
            an: (depois.find(s => /^[A-ZÀ-Ú][a-zà-ú]+ [A-ZÀ-Ú]/.test(s) && !s.includes('•')) || '').slice(0, 40)});
}
out;
