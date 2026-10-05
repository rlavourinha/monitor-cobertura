// Bradesco BBI — passo 2 da cópia: no portal (https://bradesco-portal.bluematrix.com/economics, depois de
// bradesco_chrome_empacota.js na página do documento), lê window.name, decodifica e copia para a área de transferência.
// O clipboard exige ativação do usuário NO MESMO LOTE: usar browser_batch com [computer left_click no ref do título
// "Economics" (find "Economics heading"), javascript_tool com este arquivo]. Clique num lote e cópia no seguinte falha.
// Depois: `python bradesco_relatorios.py --clip`.
const nm = window.name || '';
if (!nm.startsWith('BDX:')) throw new Error('window.name sem pacote BDX (rode bradesco_chrome_empacota.js na página do documento)');
const s = decodeURIComponent(escape(atob(nm.slice(4))));
const out = {chars: s.length, blocos: (s.match(/<<<BDX /g) || []).length, foco: document.hasFocus()};
out.api = await Promise.race([navigator.clipboard.writeText(s).then(() => 'ok'), new Promise(r => setTimeout(() => r('timeout'), 8000))]).catch(e => String(e).slice(0, 100));
if (out.api !== 'ok') { const ta = document.createElement('textarea'); ta.value = s; document.body.appendChild(ta); ta.focus(); ta.select(); out.exec = document.execCommand('copy'); ta.remove(); }
if (out.api === 'ok' || out.exec) window.name = '';
out;
