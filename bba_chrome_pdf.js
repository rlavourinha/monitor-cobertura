// Itaú BBA Smart — download do PDF COMPLETO de um relatório (modo Chrome).
// REGRA (13/11/2026): o digest sai do relatório inteiro; a página /report/<id> só mostra o primeiro parágrafo ("Resumo").
// Como chegar: na página do relatório, find "Acesse o relatório button" e computer left_click no ref (clique REAL;
// .click() por JS não abre). Abre uma ABA NOVA em /itaubba-pt/portal/viewer/<area>/<arquivo>/<nome> com o pdf.js.
// Pegue o tabId novo no tabs_context_mcp, espere ~6 s e rode ESTE trecho nessa aba; espere 6 s e feche a aba
// (tabs_close_mcp). O PDF cai na pasta Downloads (ex.: RDOR_20261112.pdf). O Chrome só deixa um download automático
// por aba; como cada visualizador é uma aba nova, funciona para vários relatórios em sequência.
// Depois de todos: `python bba_relatorios.py --pdfs "S/bba_novos.json"` casa cada PDF com o relatório pelo título.
const paginas = document.querySelectorAll('.page').length;
const b = document.getElementById('download') || document.getElementById('secondaryDownload');
if (!b) throw new Error('visualizador sem botão Save (id download): a aba é mesmo /portal/viewer/...?');
b.click();
({pdf: 'pedido', paginas, arquivo: decodeURIComponent(location.pathname.split('/').pop() || '')});
