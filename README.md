# Monitor de Cobertura

Painel local (HTML autocontido) que compara **preço**, **minha estimativa** e **consenso**
para as empresas cobertas. Universo inicial: RDOR3 e SAUD3.

## Rodar

```
pip install -r requirements.txt
atualizar.bat tudo       # primeira carga (COTAHIST anual, Focus longo, tudo)
atualizar.bat            # depois: janela diária (padrão)
```

## Painel de uma tela

A entrada é o slide **Painel**: uma tela para formar opinião. Faixa de oito números (índices, câmbio, Brent, VIX,
Treasury, Selic, NTN-B), tabela Macro (realizado × Focus × BCB para o ano corrente e o próximo, com Δ de 4 semanas),
Cobertura (preço, dia, 20 d, P/L e upside meu / consenso), **Sinais** (frases geradas por regra a partir dos dados),
**Opinião** (o que você escreve em `opiniao.json`: macro, juros, câmbio, bolsa e um campo por papel, com data),
Movimentos recentes, sparkline da NTN-B 2035 e os carimbos de atualização. Logo abaixo da faixa de cotações vem o
**Ibovespa**: gráfico do índice (janela padrão 2 anos, seletor 1a…máx) ao lado da **decomposição** (11 setores, quatro
papéis que mais puxaram e quatro que mais seguraram) para dia / 5 d / 20 d / ano. **Clicar num ponto do gráfico**
recalcula no navegador a decomposição daquela data até hoje (chip "desde dd/mm/aa"), com a mesma identidade do índice
usada em Python (Σ quantidade teórica × preço ÷ redutor); os dados (carteira, 5 anos de fechamentos dos 76 papéis,
proventos, fotografias diárias) vão embutidos no HTML em `#ibov-dados` (~1,3 MB). Janelas que cruzam um rebalanceamento
(jan/mai/set) sem fotografia daquele dia caem para peso atual × retorno e mostram o erro contra o índice, que cresce
com o tamanho da janela (≈0,3 p.p. em um mês, 7 a 14 p.p. em um a quatro anos). Papéis que trocaram de código sem
mudar a ação (`config.ALIAS_TICKER`: AXIA3←ELET3, EMBJ3←EMBR3, NATU3←NTCO3, MOTV3←CCRO3, MBRF3←MRFG3) têm a série
emendada em `b3.serie`; os que não existiam na data (ex.: AZZA3, BRAV3, ISAE4) são listados na linha de cabeçalho.
Antes de jul/2021 não há fechamentos dos papéis e o clique avisa. Os demais slides são o detalhe; a decomposição
completa (12 maiores e 12 menores contribuições) está no slide "Ibovespa · decomposição".

**Como o erro é zerado.** A identidade só fecha quando se conhece a quantidade teórica de cada papel na data
inicial. Fontes, em ordem de qualidade:
1. **Fotografia diária da carteira** (`data/ibov_carteira/AAAA-MM-DD.json`: quantidade, peso e redutor; a B3 só
   publica a do dia, então a coleta diária acumula desde 25/09/2026). Com fotografia no dia, erro de ~0,01 p.p.
   Fotografia de outro dia do mesmo quadrimestre também serve: a quantidade é levada até a data pelo ajuste de
   proventos (dividendos e JCP mudam a quantidade teórica na data ex; bonificação e desdobramento também).
2. **Reconstrução por proventos** dentro do quadrimestre corrente, a partir da carteira de hoje (sem fotografia).
3. **Peso atual × retorno** quando a janela cruza um rebalanceamento sem fotografia: aproximado, erro declarado.
Com fotografia, papéis que saíram do índice entram com valor inicial e valor final zero, e os que entraram, com
valor inicial zero, então a soma fecha mesmo cruzando rebalanceamento. Proventos: canal da B3 nos últimos 13 meses
(com bonificações); antes disso, dividendos do Yahoo (`prov_yahoo` em `ibov_comp.json`, gravado pelo passo semanal
do DY), marcados como aproximados.

**Fotografias históricas (29, de 2019 a 2026).** A B3 não publica o histórico, mas ele existe espalhado:
- **Wayback Machine, site antigo da B3** (`bvmf.bmfbovespa.com.br/indices/ResumoCarteiraTeorica.aspx` e
  `ResumoCarteiraQuadrimestre.aspx`): tabela completa com quantidade, peso e redutor. Cobre jan/2019 a mar/2021; depois
  a página congelou (as capturas de 2022–2026 ainda mostram o quadrimestre jan–abr/2021). `python -m fontes.ibov_wayback`.
- **Wayback, API nova** (`GetPortfolioDay`): só uma captura completa (31/03/2025); as ~30 mensais são a página 1.
- **GitHub, arquivos `IBOVDia_dd-mm-aa.csv`** que usuários baixaram da B3 e commitaram: set e out/2021, jun/2023,
  mar/2024, ago/2024, dez/2024, jun e jul/2025. Alguns vêm sem o rodapé; o redutor é derivado
  (Σ quantidade × fechamento ÷ Ibovespa do dia, `ibov_wayback.preenche_redutores`, campo `redutor_derivado`).
- **B3 ao vivo:** todo dia desde 25/09/2026.
Quadrimestres ainda sem fotografia: 2022 inteiro, jan–abr e set–dez/2023, set/2025 a ago/2026. Nesses, janela que
cruza o rebalanceamento continua aproximada. Para fechar, precisa de mais arquivos IBOVDia dessas datas (qualquer
`IBOVDia_*.csv` baixado da B3 na época serve: basta pôr o arquivo em `data/ibov_carteira/` no formato das outras
fotografias) ou da carteira histórica de um terminal.
Códigos antigos nas fotografias são traduzidos por `config.ALIAS_TICKER` (ELET3→AXIA3, ALSO3→ALOS3, ARZZ3→AZZA3,
RRRP3→BRAV3, TRPL4→ISAE4, BRDT3→VBBR3...), e `b3.serie` emenda as séries de preço; o código antigo prevalece
enquanto negociou (NATU3 existiu antes de 2020 e voltou em 2025).

**Quem puxou, quem segurou.** Classes da mesma empresa somadas (PETR3+4, BBDC3+4); pelo menos quatro nomes de cada
lado, mais todo nome com contribuição acima de 0,5 p.p., até oito. A linha acima das tabelas diz quanto do movimento
bruto os nomes mostrados explicam. Layout: os slides usam até 1760 px de largura; acima de 1700 px as fontes do
Painel aumentam.

## Atualização automática (GitHub Actions)

Repositório público `rlavourinha/monitor-cobertura` (só dados públicos e produção própria; nada da Bloomberg entra
no repositório: a ponte `fontes/bloomberg.py` é só código e o CSV que ela gera fica fora do git). O workflow
`.github/workflows/atualizar.yml` roda as quatro janelas na nuvem, sem depender da máquina local: intraday (10h, 12h,
14h, 16h, 18h BRT em dias úteis), diário (19h45 BRT), semanal (segunda 8h45) e mensal (8h todo dia). Cada execução
roda `coletar.py --janela X`, gera `output/monitor.html`, faz commit dos caches (`data/`, sem os zips da CVM) e do
HTML de volta na branch `main` e publica o painel em **https://rlavourinha.github.io/monitor-cobertura/**. Para rodar
à mão: aba Actions → "atualizar" → Run workflow → janela. O agendamento local (`agendar.ps1`) continua disponível como
alternativa, mas não é necessário. Enviar o arquivo de workflow exige o escopo `workflow` no token do `gh`
(`gh auth refresh -h github.com -s workflow`).

## Dividend yield do Ibovespa

Slide "Ibovespa · dividend yield" (seção Macro) e chip "DY" no tile do Ibovespa no Painel. Duas séries em
`data/ibov_dy.json`:

- **Exato** (passo `Ibovespa DY` da janela diária): DY 12 m = Σ q_i·D_i ÷ Σ q_i·p_i, com q = quantidade teórica da
  carteira, D = dividendos + JCP brutos por papel com data-com nos últimos 12 meses (canal de listadas da B3, que só
  devolve ~13 meses) e p = último fechamento oficial. Um ponto por dia, acumulando a partir de 25/09/2026. Guarda
  também o DY e a contribuição de cada papel (peso × DY).
- **Aproximado** (passo `Ibovespa DY histórico` da janela semanal): mensal desde 2010, com dividendos e fechamentos do
  Yahoo (ajustados por desdobramento) de cada papel da carteira **atual**, ponderados pela quantidade de hoje. Serve
  para nível relativo, não para o número do mês: a composição muda a cada quadrimestre, o Yahoo mistura JCP e
  dividendos e papéis sem 12 meses de história ficam fora do mês.

Nenhuma das séries inclui recompras. Para um "shareholder yield" seria preciso a DFC das companhias (CVM, ITR/DFP,
linha de aquisição de ações em tesouraria) ou o informe mensal de negociação de ações próprias.

## Opinião qualificada (segunda seção)

Cartas de gestão de bons fundos, uma ficha por fundo em `cartas/<fundo>.json` (fontes com URL, data, desempenho,
cenário global e Brasil, tese longa, posições por book, "minha leitura"). Cada ficha vira dois slides logo após o
Painel. Primeiro fundo: Itaú Janeiro (relatório mensal de 31/08/2026 e cartas trimestrais de mar/26, dez/25 e set/25).
Para acrescentar um fundo: baixar a carta (PDF → `pdftotext -layout`), preencher o JSON, `python build.py`.

**Performance explicada pelas cartas**: cota diária do fundo pela CVM (`fontes/cvm_inf_diario.py`, informe diário por
CNPJ, cache em `data/inf_diario/`, passo `cotas de fundos CVM` da janela diária) e CDI diário (SGS 12) capitalizado.
O campo `periodos_cartas` da ficha (ini, fim, rótulo, cor, carta, tipo, cenário, resultado) pinta a linha do fundo e a
faixa de fundo de cada trimestre com a cor da carta que o explica; a tabela abaixo do gráfico traz cenário e o que
explicou o resultado, na mesma cor. Períodos sem carta ficam em cinza.

**Cartas trimestrais**: o campo `cartas_trimestrais` da ficha (ref, data, publicada, título, pontos) vira o slide
"cartas trimestrais", uma coluna por carta e a tese longa no rodapé. As cartas do Itaú Janeiro saem no LinkedIn do gestor
(Bruno Serra Fernandes, aba Documents) como documento nativo; o visualizador do LinkedIn carrega um *transcript manifest*
com o texto por página, que dá para ler no navegador logado sem baixar o PDF (o site da Itaú Asset sobrescreve os
relatórios mensais e não guarda as cartas antigas).

**Linha do tempo por fundo**: o campo `linha_do_tempo` da ficha (data, fonte, tipo primária/imprensa, chamadas de Selic e
IPCA, juros, inflação, câmbio, posições) vira o slide "evolução da visão", com a chamada de Selic do gestor contra o Focus
e a Selic realizada. Os relatórios mensais da Itaú Asset são sobrescritos no mesmo endereço; edições antigas só pelo Wayback
(`web.archive.org/cdx/search/cdx?url=...`) ou por entrevistas do gestor.

**Carteiras pela CVM (`config.FUNDOS_CVM`)**: a CVM publica a composição mensal de todos os fundos (CDA, dados abertos,
`cda_fi_AAAAMM.zip`, 15–30 MB). O gestor pode manter as posições em sigilo por 180 dias (`DT_CONFID_APLIC`); vencido o prazo,
a CVM regrava o arquivo do mês com as posições abertas, então a carteira completa chega com ~6 meses de defasagem. Usar o
CNPJ do **master** (o feeder só carrega cotas). Janela mensal, cache em `data/cda/`. Slide por fundo com 12 maiores pesos,
tabela com variação contra o mês anterior e papéis que saíram. Primeiro fundo: Dynamo Cougar Master (37.916.879/0001-26).

## Juros nominais

- **Curva de hoje**: ANBIMA, estrutura a termo prefixada e IPCA (vértices em dias úteis, parâmetros de Svensson) e
  inflação implícita. O endpoint só serve os últimos ~5 pregões, então cada dia vira fotografia em `data/ettj/`.
- **Histórico**: curva de vencimento constante (1, 2, 3, 5, 7, 10 anos prefixada; 2, 5, 10, 20, 30 anos real) interpolada
  entre os títulos do Tesouro Direto de cada dia, desde 31/12/2004 (`fontes/curva.py` → `data/curva_tesouro.json`).
  Sem extrapolação. Derivados: inclinação 5a − 1a, inflação implícita 5a, Selic real ex-ante (pré 1a vs Focus 12 m).
- **FRAs**: taxas a termo de 1 ano ano a ano da curva ANBIMA (FRA(t1,t2) = ((1+r2)^t2/(1+r1)^t1)^(1/(t2−t1)) − 1),
  tabela 1a1a / 2a1a / 3a2a / 5a5a / 2a3a com variação contra a fotografia anterior, **Selic implícita** mês a mês pela
  Svensson da ANBIMA (λ·t, t = du/252) contra a Selic esperada pelo Focus por reunião, e histórico dos FRAs de vencimento
  constante desde 2004.
- Séries de swap DI × pré do BCB (7805/7806) pararam em 2019; as páginas de DI futuro da B3 não respondem por script.

## Janelas de atualização

Cada fonte grava seu arquivo e registra hora e resultado em `data/estado.json`. Uma fonte que falha não derruba
as outras: o dado anterior fica e a capa mostra o carimbo em vermelho ("falhou" ou mais velho que o limite).

| Janela | Quando | O que coleta | Custo |
|---|---|---|---|
| `intraday` | a cada hora, 10h–19h, dias úteis | cotações com atraso (universo, índices, movimentos) | ~10 s |
| `diario` | 19h30 | COTAHIST **do dia** (COTAHIST_D, ~0,5 MB, só pregões faltantes), nº de ações, Tesouro, SGS diários (Selic, PTAX), Yahoo 5 anos, consenso Yahoo, minhas estimativas (modelo E-A), cotações | ~1 min |
| `semanal` | segunda 08h30 | Focus completo (anuais, mensais, trimestrais, 12/24m, Selic, Top 5) + séries longas do ano corrente | ~2 min |
| `mensal` | dia a dia às 08h (as séries só mudam no mês) | SGS mensais/anuais (IPCA, PIB, IBC-Br, crédito, CAGED) + realizado anual | ~1 min |
| `tudo` | primeira carga | todas acima; COTAHIST anual se o cache não existir | ~5 min |
| sob demanda | quando você roda | `--ponte` / `--consenso-bloomberg` (terminal), `macro_bcb/rpm.json` (a cada RPM), `estimativas/minhas.csv` | — |

Agendamento no Windows (usuário atual, sem admin): `powershell -ExecutionPolicy Bypass -File .\agendar.ps1`
registra quatro tarefas que chamam `atualizar.bat <janela>` (coleta + build). `-Remover` desfaz.
Os caches imutáveis (COTAHIST de anos passados, Focus longo de anos encerrados) nunca são rebaixados.

Abra `output/monitor.html` no navegador. Formato de apresentação: trilho lateral com as seções
(Macro, Setorial, Cobertura, uma por empresa), um slide por tema, navegação por ↑/↓ ou PgUp/PgDn,
deep-link por `#id` (ex.: `#RDOR3-preco`).

**Gráficos de linha interativos:** a série completa vai embutida no HTML e o navegador redesenha o gráfico
(`setupLinhas` no JS, espelho de `svg_linhas`). Cada gráfico tem chips 1a/2a/3a/5a/10a/máx (chips maiores que a série
ficam desabilitados) e o trilho tem um seletor global. Padrão = máximo. Ibovespa desde 1993, S&P desde 1970, VIX desde
1990 (Yahoo com `period1=0`; `range=max` devolveria mensal). Focus (revisões) desde 2021. Crosshair, banda mín–máx e
rótulos são recalculados a cada janela.

**Tipografia:** Playfair Display (títulos e ledes) + Source Sans 3 (corpo, tabelas, SVG), tudo em itálico
(`config.ESTILO_ITALICO`). As fontes são **embutidas** no HTML (`assets/fonts.css`, base64, ~390 KB), então o painel
renderiza igual atrás de firewall e offline. Para trocar: editar `config.FONTES_GOOGLE` / `FONTE_TITULO` / `FONTE_CORPO`
e rodar `python tipografia.py` (baixa do Google Fonts e regrava o CSS).

Checagem de layout (Playwright headless, 1440×900, rede bloqueada): ver o script no histórico da sessão
ou rodar `python build.py` e conferir `overflow=0` / `colisoes_texto=0` por slide.

## Fontes (prioridade)

| O quê | Fonte | Cadência | Arquivo |
|---|---|---|---|
| Preço histórico | B3 COTAHIST (fechamento oficial) | diário; só o ano corrente é rebaixado | `data/cotahist/{ano}.csv` |
| Intraday | Yahoo Finance, atraso ~15 min | a cada execução | `data/mercado.json` |
| Nº de ações | B3 canal de listadas (atual, inclui tesouraria) | a cada execução | `data/mercado.json` |
| Minha estimativa | `estimativas/minhas.csv` **ou** aba E-A do modelo Excel | quando eu publico | `estimativas/minhas.csv` |
| Consenso | Bloomberg (planilha-ponte) > Yahoo (fallback) | quando rodo o export / a cada execução | `consenso/consenso.csv` |

Os CSVs de estimativa são **históricos**: cada linha tem `data`. Nada é sobrescrito; o painel
usa a última data e plota as anteriores no gráfico de revisões.

## Camada Macro (aba "Macro")

| Bloco | Fonte | Detalhe |
|---|---|---|
| Cotações (Ibov, S&P, Nasdaq, Stoxx, USD/BRL, Brent, VIX, Treasury 10a) | Yahoo, atraso ~15 min | `config.MERCADO` |
| Focus vs realizado + revisões diárias | BCB Olinda `ExpectativasMercadoAnuais` (mediana, baseCalculo 0) | `config.FOCUS_INDICADORES`, desde `FOCUS_DESDE` |
| Selic por reunião do Copom | BCB Olinda `ExpectativasMercadoSelic` | última data disponível |
| Realizado (IPCA, Selic meta, PTAX, PIB, IBC-Br, crédito, CAGED) | BCB SGS | `config.SGS_SERIES`; janelas de 9 anos (limite da API) e novas tentativas (502 intermitente) |
| Juro real (curva NTN-B + histórico 2035) | Tesouro Transparente, CSV do Tesouro Direto | cache diário `data/tesouro_direto.csv` (~15 MB) |
| Ibov = Lucro ÷ (Ke − g) | P/L à frente do Ibov: Bloomberg (aba `macro` da ponte) ou `config.IBOV_PE_FWD` | mostra earnings yield e EY − NTN-B |
| Movimentos recentes | Yahoo (avulsos, `config.MOVIMENTOS`) + COTAHIST (cobertura) | dia, 5 e 20 pregões |

**Focus completo** (nove slides): três painéis por grupo (`config.FOCUS_GRUPOS`, 28 indicadores × 2026–2030 com Δ 4 sem e Δ 3 m),
dispersão entre respondentes (mín–máx, mediana, média, n), trajetória mensal (IPCA realizado + 24 meses esperados, câmbio,
desocupação), trajetória trimestral (PIB, IPCA, desocupação, câmbio), inflação 12 e 24 meses à frente (suavizada, histórico
desde 2024 + aberturas), Top 5 contra o mercado (anual e Selic por reunião). Recursos Olinda usados: Anuais, Mensais,
Trimestrais, Inflacao12Meses, Inflacao24Meses, Selic, Top5Anuais, Top5Selic. Top5Selic usa campos em minúsculas.

**Assertividade do Focus** (cinco slides): `python coletar.py --so-focus-longo` baixa, para IPCA, Selic, PIB e câmbio,
a série diária do Focus de cada ano de referência desde 2002 (cache imutável em `data/focus_longo/`, ~120 mil pontos) e
o realizado anual do SGS (`data/realizado.json`: IPCA = produto dos 12 meses de 433; Selic = meta em 31/12 de 432; câmbio =
último PTAX de 1; PIB = 7326). O builder mede, por horizonte (`config.FOCUS_ASSERT_HORIZONTES`, meses antes do fim do ano),
erro absoluto médio, viés (mediana − realizado) e % dos anos em que o realizado caiu dentro da faixa mín–máx dos respondentes.
Gráfico: uma linha por ano, sombra ±EAM, linha grossa = viés, laranja = último ano fechado.

**BCB vs Focus**: `macro_bcb/rpm.json` guarda as projeções do Relatório de Política Monetária (trajetória do IPCA em 4
trimestres, premissas, comparáveis e pontos do Copom). Atualizar a cada RPM (mar/jun/set/dez): baixar o PDF, `pdftotext -layout`,
preencher o JSON. Gera os slides "BCB · cenário de referência" e "BCB vs Focus".

Sem fonte automática ainda: CDS Brasil (vem pela ponte Bloomberg), minério de ferro (TIO=F inconsistente),
carteiras recomendadas e podcasts (curadoria manual).

`python coletar.py --so-macro` recoleta só essa camada (1 a 2 min). A aba **Setorial** é placeholder.

## Minha estimativa

Opção A, manual: edite `estimativas/minhas.csv` (R$ milhões; `eps` e `target` em R$/ação).
Acrescente uma linha nova com a data de hoje a cada revisão, não edite a antiga.

Opção B, do modelo: em `config.UNIVERSO["RDOR3"]["modelo"]` aponte
`{"arquivo": r"...\RDORmod.xlsx", "aba": "E-A"}`. O leitor procura rótulos por regex
(`lucro líquido`, `ebitda`, `receita`, `preço-alvo`) na coluna B e anos na linha de cabeçalho.

## Consenso Bloomberg (na máquina com terminal)

```
python coletar.py --ponte                 # gera consenso/ponte_bloomberg.xlsx com fórmulas BDP
(abrir no Excel com o add-in, esperar calcular, salvar)
python coletar.py --consenso-bloomberg    # lê os valores e acrescenta em consenso/consenso.csv
```

Campos: BEST_SALES, BEST_EBITDA, BEST_NET_INCOME, BEST_EPS (override `{ano}FY`),
BEST_TARGET_PRICE, TOT_ANALYST_REC. Conferir escala (milhões) no terminal na 1ª vez.

**Licença:** dado Bloomberg não sai da firma. Repositório privado; o painel fica local
(OneDrive compartilhado), não em GitHub Pages.

## Colaboração

- Modelos Excel: OneDrive/SharePoint, um dono por arquivo.
- Código + CSVs de estimativas/consenso: repositório privado (histórico com autor).
- Quem tem Bloomberg roda o export; um agendamento diário roda `atualizar.bat` e salva o HTML na pasta compartilhada.

## Estrutura

```
config.py          universo, anos fiscais, schema, campos Bloomberg
coletar.py         orquestra as fontes -> data/, estimativas/, consenso/
build.py           gera output/monitor.html (SVG inline, JS puro, sem CDN)
fontes/b3.py       COTAHIST + nº de ações
fontes/yahoo.py    intraday + consenso fallback (yfinance)
fontes/bloomberg.py ponte BDP (gera/lê)
fontes/modelo_ea.py leitor da aba E-A
```

## Limites conhecidos

- Yahoo não entrega EBITDA de consenso nem histórico de revisões; o histórico nasce das coletas diárias.
- SAUD3 (Bradsaúde) negocia desde mai/2026; ODPV3 é outra entidade e não é emendada.
- Preço não ajustado por proventos (P/L e upside usam preço cheio, como deve ser).
- Nº de ações é só o atual; P/L de anos passados não é calculado.
