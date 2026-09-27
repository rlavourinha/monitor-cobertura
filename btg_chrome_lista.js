// Modo Chrome do coletor BTG (roda via javascript_tool da extensão Claude in Chrome, numa aba do portal logado).
// Devolve os relatórios dos últimos DIAS dias que NÃO estão em CONHECIDOS (ids já indexados: `python btg_relatorios.py --conhecidos`).
// Substituir CONHECIDOS e DIAS antes de executar.
const CONHECIDOS = new Set([]);
const DIAS = 3;
const tok = localStorage.getItem('accessToken'); const raw = tok && tok.startsWith('"') ? JSON.parse(tok) : tok;
const H = {accept: 'application/json', 'content-type': 'application/json', 'culture-code': 'en_us', 'root-version': '30', authorization: 'Bearer ' + raw};
const r = await fetch('https://api.portal-research.btgpactual.com/research/Document/SimpleFilterImproved?department=ALL&number_of_rows=100&start_number_row=1', {headers: H});
if (r.status !== 200) throw new Error('lista HTTP ' + r.status + ' (sessão do portal expirou?)');
const j = await r.json();
const lim = new Date(Date.now() - DIAS * 864e5).toISOString().slice(0, 10);
(j.selected_documents || []).filter(d => !CONHECIDOS.has(d.document_id) && d.publish_date.slice(0, 10) >= lim)
  .map(d => ({id: d.document_id, d: d.publish_date.slice(0, 10), co: (d.company_sector || '').replace(/\s+/g, ' ').trim(), t: d.title, an: d.main_analyst, rating: d.rating || '', kb: +d.file_size || 0}));
