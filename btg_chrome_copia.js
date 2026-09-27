// Modo Chrome do coletor BTG — PASSO B (cópia). Copia para a área de transferência do Windows APENAS o texto dos PDFs
// já extraído pelo passo A (window.__blocos). O clipboard exige foco: clicar uma vez num ponto neutro da página
// (ex.: computer left_click em (100, 650), faixa cinza do menu) imediatamente antes de executar este trecho.
// Depois: `python btg_relatorios.py --clip` grava data/btg/txt/<id>.txt.
const s = (window.__blocos || []).join('\n');
if (!s) throw new Error('window.__blocos vazio: rode o passo A antes');
const out = {chars: s.length, blocos: window.__blocos.length};
out.api = await Promise.race([navigator.clipboard.writeText(s).then(() => 'ok'), new Promise(r => setTimeout(() => r('timeout'), 8000))]).catch(e => String(e).slice(0, 100));
if (out.api !== 'ok') { const ta = document.createElement('textarea'); ta.value = s; document.body.appendChild(ta); ta.focus(); ta.select(); out.exec = document.execCommand('copy'); ta.remove(); }
out;
