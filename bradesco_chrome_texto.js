// Bradesco BBI — texto integral de um relatório (modo Chrome). O portal mostra o relatório num iframe de outra origem;
// a página do iframe abre direto e tem o texto todo em HTML (sem precisar do PDF). Rodar DEPOIS de navegar para
//   https://bradescobbi.bluematrix.com/links2/doc/html/<id>      (id = o mesmo uuid de /report/<id>)
// e esperar ~6 s. Lê document.body.innerText (só DOM), corta cabeçalho de navegação e disclosures, e acumula em
// sessionStorage['bdx_blocos'] (origem bradescobbi.bluematrix.com) como "<<<BDX id>>>\ntexto".
// Repetir para cada relatório (navigate → wait → este trecho). No fim: bradesco_chrome_empacota.js (window.name), navegar
// para o portal e bradesco_chrome_copia.js no mesmo lote de um clique; depois `python bradesco_relatorios.py --clip`.
const id = (location.pathname.match(/([0-9a-f-]{36})/) || [])[1];
let body = document.body.innerText.replace(/\r/g, '');
body = body.replace(/^\s*REPORT CONTENT\s*DISCLOSURES\s*VIEW PDF\s*/i, '');
const fimIdx = ['\nANALYST CERTIFICATION', '\nAnalyst Certification', '\nIMPORTANT DISCLOSURES', '\nImportant Disclosures', '\nDISCLOSURES\n', '\nDisclosures\n', '\nRating Distribution', '\nBradesco S.A. Corretora de Títulos']
  .map(k => body.indexOf(k, 300)).filter(i => i > 0);
const fim = fimIdx.length ? Math.min(...fimIdx) : body.length;
const texto = body.slice(0, fim).replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim().slice(0, 16000);
const bloco = '<<<BDX ' + id + '>>>\n' + texto;
const acc = sessionStorage.getItem('bdx_blocos') || '';
sessionStorage.setItem('bdx_blocos', acc + (acc ? '\n' : '') + bloco);
({id, chars: texto.length, acumulado: sessionStorage.getItem('bdx_blocos').length, titulo: document.title.slice(0, 60)});
