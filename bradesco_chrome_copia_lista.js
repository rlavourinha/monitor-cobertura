// Bradesco BBI — copia a lista de NOVOS (sessionStorage['bdx_novos'], gravada por bradesco_chrome_lista.js) para a área
// de transferência, porque a saída do javascript_tool é truncada em ~1.000 caracteres. Rodar na aba da Busca Avançada,
// em browser_batch com [computer left_click no ref de um título real (find "Advanced Search Results"), este arquivo]:
// o clipboard exige ativação do usuário no mesmo lote.
// Depois: `python bradesco_relatorios.py --novos clip --dias 3 > S/bradesco_novos.json`.
const s = sessionStorage.getItem('bdx_novos') || '[]';
const out = {chars: s.length, itens: JSON.parse(s).length, foco: document.hasFocus()};
out.api = await Promise.race([navigator.clipboard.writeText(s).then(() => 'ok'), new Promise(r => setTimeout(() => r('timeout'), 8000))]).catch(e => String(e).slice(0, 100));
if (out.api !== 'ok') { const ta = document.createElement('textarea'); ta.value = s; document.body.appendChild(ta); ta.focus(); ta.select(); out.exec = document.execCommand('copy'); ta.remove(); }
out;
