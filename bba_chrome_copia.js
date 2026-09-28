// Itaú BBA Smart — copia os resumos acumulados (sessionStorage['bba_blocos']) para a área de transferência e limpa.
// O clipboard exige foco: dar um computer left_click num ponto neutro da página logo antes (ex.: (100, 650)).
// Depois: `python bba_relatorios.py --clip`.
const s = sessionStorage.getItem('bba_blocos') || '';
if (!s) throw new Error('sessionStorage bba_blocos vazio: rode bba_chrome_resumo.js em cada relatório antes');
const out = {chars: s.length, blocos: (s.match(/<<<BBA /g) || []).length};
out.api = await Promise.race([navigator.clipboard.writeText(s).then(() => 'ok'), new Promise(r => setTimeout(() => r('timeout'), 8000))]).catch(e => String(e).slice(0, 100));
if (out.api !== 'ok') { const ta = document.createElement('textarea'); ta.value = s; document.body.appendChild(ta); ta.focus(); ta.select(); out.exec = document.execCommand('copy'); ta.remove(); }
if (out.api === 'ok' || out.exec) sessionStorage.removeItem('bba_blocos');
out;
