"""Recorte do texto de relatório de research antes da leitura (vale para todas as casas).

1. Avisos legais: autorização dele (26/11/2026) para não ler nenhum aviso legal. Quando começam (analyst
   certification, disclosures, disclaimer, informações relevantes...), todo o resto do documento é aviso: corta ali.
2. Relatório genérico longo (setorial, outlook, vários países): fica com o começo (tese/resumo) e as páginas que citam
   empresas da cobertura, mais a página seguinte a cada uma (a seção da empresa costuma continuar). Relatório em que a
   maioria das páginas é sobre a cobertura (ex.: um aprofundado de Rede D'Or) fica inteiro.

Os textos têm marcadores "[página N]" (pypdf). Sem marcadores, só o corte de avisos se aplica.
"""
from __future__ import annotations

import re

# linha que ABRE a seção de avisos (no começo da linha). Achados nos PDFs reais:
#   JPM/BofA "Analyst Certification:"; BTG "Disclosures"; Itaú BBA "Relevant Information", "DISCLAIMER",
#   "Informações Relevantes"; Goldman "Disclosure Appendix"; Morgan Stanley "Disclosure Section".
_AVISO = re.compile(
    r"^[ \t]*(?:"
    r"Analyst Certifications?[ \t]*(?::|$)|"
    r"(?:Important |Required |Other |General )?Disclosures?[ \t]*:?[ \t]*$|"
    r"Disclosure (?:Appendix|Section)\b|"
    r"Important Disclosures? (?:Appendix|Information)\b|"
    r"Disclaimers?[ \t]*:?[ \t]*$|"
    r"Relevant Information[ \t]*:?[ \t]*$|"
    r"Informa[çc][õo]es (?:Relevantes|Importantes)[ \t]*:?[ \t]*$|"
    r"Avisos? Legais?[ \t]*:?[ \t]*$|"
    r"Declara[çc][ãa]o do[s]? Analista[s]?[ \t]*:?[ \t]*$"
    r")", re.I | re.M)
_NAO_E_AVISO = re.compile(r"begin on page|see page|on page \d|refer to|página \d|\.{4,}|\s\d{1,3}\s*$", re.I)
_PAG = re.compile(r"^\[página (\d+)\]\s*$", re.M)

PAGINAS_GENERICO = 20     # abaixo disso não vale recortar por empresa
PAGINAS_INICIO = 4        # tese/resumo do começo, sempre mantidos
PARCELA_FOCO = 0.5        # se ≥ metade das páginas cita a cobertura, o relatório é "da cobertura": fica inteiro


def corta_avisos(texto: str, inicio_min: float = 0.15) -> tuple[str, str | None]:
    """(texto sem avisos, nota). Ignora marcadores no primeiro `inicio_min` do texto (capa: "disclosures begin on page...")."""
    for m in _AVISO.finditer(texto):
        if m.start() < len(texto) * inicio_min:
            continue
        linha = texto[m.start():texto.find("\n", m.start()) if "\n" in texto[m.start():] else len(texto)]
        if _NAO_E_AVISO.search(linha):
            continue
        pags = _PAG.findall(texto[:m.start()])
        pag = pags[-1] if pags else "?"
        return texto[:m.start()].rstrip(), f"avisos legais omitidos a partir da página {pag} ({linha.strip()[:40]})"
    return texto, None


def _paginas(texto: str) -> list[tuple[int, str]]:
    marcas = list(_PAG.finditer(texto))
    out = []
    for i, m in enumerate(marcas):
        fim = marcas[i + 1].start() if i + 1 < len(marcas) else len(texto)
        out.append((int(m.group(1)), texto[m.start():fim]))
    return out


def foca_cobertura(texto: str, padroes: dict[str, str]) -> tuple[str, str | None]:
    """Relatório genérico longo -> começo + páginas que citam a cobertura (e a seguinte de cada uma)."""
    pags = _paginas(texto)
    if len(pags) < PAGINAS_GENERICO:
        return texto, None
    cabeca = texto[:texto.find("[página ")] if "[página " in texto else ""
    cita = {}
    for n, t in pags:
        # conta a página se a empresa aparece 2+ vezes ou no topo (título da seção): tabela comparativa com todas as
        # empresas do setor cita cada uma uma vez só e não deve puxar a página
        hit = sorted({tk for tk, rx in padroes.items()
                      if len(re.findall(rx, t, re.I)) >= 2 or re.search(rx, t[:300], re.I)})
        if hit:
            cita[n] = hit
    if len(cita) >= PARCELA_FOCO * len(pags):
        return texto, None
    nums = [n for n, _ in pags]
    manter = set(nums[:PAGINAS_INICIO])
    for n in cita:
        manter.add(n)
        i = nums.index(n)
        if i + 1 < len(nums):
            manter.add(nums[i + 1])
    partes, ultimo = [], None
    for n, t in pags:
        if n in manter:
            if ultimo is not None and n != ultimo + 1:
                partes.append(f"[... páginas {ultimo + 1}-{n - 1} omitidas: não citam a cobertura ...]\n")
            partes.append(t)
            ultimo = n
    empresas = sorted({tk for v in cita.values() for tk in v})
    nota = (f"relatório genérico de {len(pags)} páginas: mantidas {len(manter)} (início + páginas que citam "
            f"{', '.join(empresas) or 'nenhuma empresa da cobertura'})")
    return cabeca + "".join(partes).rstrip(), nota


def recorta(texto: str, padroes: dict[str, str]) -> tuple[str, list[str]]:
    """Aplica os dois recortes. Devolve (texto, notas)."""
    notas = []
    texto, n1 = corta_avisos(texto)
    if n1:
        notas.append(n1)
    texto, n2 = foca_cobertura(texto, padroes)
    if n2:
        notas.append(n2)
    return texto, notas
