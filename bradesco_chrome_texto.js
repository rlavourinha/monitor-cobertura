// Bradesco BBI — texto da página + download do PDF COMPLETO de um relatório (modo Chrome).
// REGRA (13/11/2026): o digest sai do PDF inteiro. A página HTML só tem a prosa (as tabelas são imagem) e fica de reserva.
// Rodar DEPOIS de navegar para
//   https://bradescobbi.bluematrix.com/links2/doc/html/<id>      (id = o mesmo uuid de /report/<id>)
// e esperar ~6 s. O Chrome só deixa UM download automático por aba, então CADA RELATÓRIO RODA NUMA ABA NOVA:
//   tabs_create_mcp → navigate → wait 6 s → este trecho → wait 6 s → tabs_close_mcp.
// Por isso o acumulado fica em localStorage['bdx_blocos'] (compartilhado entre abas da origem bradescobbi.bluematrix.com),
// como "<<<BDX id>>>\ntexto". O PDF cai na pasta Downloads com o título do relatório como nome.
// No fim: numa página links2/doc/html/... rodar bradesco_chrome_empacota.js (window.name), navegar para o portal e
// bradesco_chrome_copia.js no mesmo lote de um clique; depois `python bradesco_relatorios.py --clip` e
// `python bradesco_relatorios.py --pdfs "S/bradesco_novos.json"` (troca o texto da página pelo do PDF completo).
const id = (location.pathname.match(/([0-9a-f-]{36})/) || [])[1];
let body = document.body.innerText.replace(/\r/g, '');
body = body.replace(/^\s*(REPORT CONTENT|SUMMARY)\s*DISCLOSURES\s*VIEW PDF\s*/i, '');
const fimIdx = ['\nANALYST CERTIFICATION', '\nAnalyst Certification', '\nIMPORTANT DISCLOSURES', '\nImportant Disclosures', '\nDISCLOSURES\n', '\nDisclosures\n', '\nRating Distribution', '\nBradesco S.A. Corretora de Títulos', '\nExpand All Disclosures']
  .map(k => body.indexOf(k, 300)).filter(i => i > 0);
const fim = fimIdx.length ? Math.min(...fimIdx) : body.length;
const texto = body.slice(0, fim).replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim().slice(0, 16000);
const bloco = '<<<BDX ' + id + '>>>\n' + texto;
const acc = localStorage.getItem('bdx_blocos') || '';
localStorage.setItem('bdx_blocos', acc + (acc ? '\n' : '') + bloco);
// PDF completo: o mesmo arquivo do botão "View PDF" (só DOM: um link com download, clicado na própria página)
const a = document.createElement('a'); a.href = '/links2/secure/doc/pdf/' + id; a.download = ''; document.body.appendChild(a); a.click(); a.remove();
({id, chars: texto.length, acumulado: localStorage.getItem('bdx_blocos').length, pdf: 'pedido', titulo: document.title.slice(0, 60)});
