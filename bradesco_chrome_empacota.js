// Bradesco BBI — passo 1 da cópia: empacota os textos acumulados (localStorage['bdx_blocos'], origem
// bradescobbi.bluematrix.com; bradesco_chrome_texto.js grava ali porque cada relatório roda numa aba nova) em window.name
// (base64), que sobrevive à navegação dentro do mesmo site, e limpa o acumulado.
// Motivo: a página do documento bloqueia a área de transferência (clipboard e execCommand falham mesmo com clique);
// no portal (bradesco-portal.bluematrix.com) a cópia funciona. Rodar numa página links2/doc/html/...; depois navegar
// NA MESMA ABA para https://bradesco-portal.bluematrix.com/economics e seguir com bradesco_chrome_copia.js.
const s = localStorage.getItem('bdx_blocos') || sessionStorage.getItem('bdx_blocos') || '';
if (!s) throw new Error('bdx_blocos vazio: rode bradesco_chrome_texto.js em cada relatório antes');
window.name = 'BDX:' + btoa(unescape(encodeURIComponent(s)));
localStorage.removeItem('bdx_blocos'); sessionStorage.removeItem('bdx_blocos');
({chars: s.length, blocos: (s.match(/<<<BDX /g) || []).length, nome: window.name.length});
