// Itaú BBA Smart — resumo de um relatório (modo Chrome). Rodar DEPOIS de navegar para
// https://www.itau.com.br/itaubba-pt/portal/<area>/report/<id> e esperar ~7 s (area = equity | macro | fi).
// Lê o "Resumo" em HTML da página (só DOM) e acumula em sessionStorage['bba_blocos'] como "<<<BBA id>>>\ntexto".
// Repetir para cada relatório (navigate → wait → este trecho). No fim: clicar num ponto neutro da página (foco) e rodar
// bba_chrome_copia.js, depois `python bba_relatorios.py --clip`. Devolve {id, chars}.
const id = (location.pathname.match(/report\/([0-9a-f-]{36})/) || [])[1];
const body = document.body.innerText;
const ini = body.indexOf('Resumo');
// corta antes da assinatura/disclaimer/caixas de cobertura (o resumo útil vem antes)
const fimIdx = ['\nBest regards', '\nPlease refer to the relevant page', '\nCobertura\n', '\nCentral de Ajuda', '\nCompanies\n', 'Quem somos', 'Relatórios relacionados']
  .map(k => body.indexOf(k, ini + 6)).filter(i => i > 0);
const fim = fimIdx.length ? Math.min(...fimIdx) : body.length;
const cab = body.slice(0, ini > 0 ? ini : 600).split('\n').map(s => s.trim()).filter(s => s && !/_outline|_base|^(Home|Equity|Fixed Income|Macro|Eventos|R|L|e|Acesse o relatório)$/.test(s));
const texto = (ini > 0 ? body.slice(ini + 6, fim) : body.slice(0, 8000)).replace(/\n(sort_up|copy_outline)\n/g, '\n').replace(/\n \n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
const bloco = '<<<BBA ' + id + '>>>\n' + [...new Set(cab)].slice(0, 6).join('\n') + '\n\nResumo\n' + texto;
const acc = sessionStorage.getItem('bba_blocos') || '';
sessionStorage.setItem('bba_blocos', acc + (acc ? '\n' : '') + bloco);
({id, chars: texto.length, acumulado: sessionStorage.getItem('bba_blocos').length});
