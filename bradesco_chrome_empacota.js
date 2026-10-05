// Bradesco BBI — passo 1 da cópia: empacota os textos acumulados (sessionStorage['bdx_blocos'], origem
// bradescobbi.bluematrix.com) em window.name (base64), que sobrevive à navegação dentro do mesmo site.
// Motivo: a página do documento bloqueia a área de transferência (clipboard e execCommand falham mesmo com clique);
// no portal (bradesco-portal.bluematrix.com) a cópia funciona. Rodar numa página links2/doc/html/...; depois navegar
// para https://bradesco-portal.bluematrix.com/economics e seguir com bradesco_chrome_copia.js.
const s = sessionStorage.getItem('bdx_blocos') || '';
if (!s) throw new Error('sessionStorage bdx_blocos vazio: rode bradesco_chrome_texto.js em cada relatório antes');
window.name = 'BDX:' + btoa(unescape(encodeURIComponent(s)));
({chars: s.length, blocos: (s.match(/<<<BDX /g) || []).length, nome: window.name.length});
