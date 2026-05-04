import io
import json
import logging
import os

import pdfplumber
import requests
from openai import OpenAI

from services.supabase_client import client
from services.whatsapp import _send
from services.constants import (
    OPENAI_MODEL,
    STATUS_NOVO,
    STATUS_ANALISADO,
    STATUS_CONTATADO,
    STATUS_COMPORTAMENTAL_RECEBIDO,
    STATUS_ENCAMINHADO,
)

log = logging.getLogger(__name__)

GRUPO_FRANQUEADOS   = os.getenv("GRUPO_FRANQUEADOS")
LINK_COMPORTAMENTAL = os.getenv("LINK_COMPORTAMENTAL")


# --- helpers privados ---

def _get_openai() -> OpenAI:
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY não configurada.")
    return OpenAI(api_key=key)


def _extrair_texto_pdf(url: str) -> str:
    try:
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
            return "\n".join(p.extract_text() or "" for p in pdf.pages).strip()
    except Exception as e:
        log.error(f"Erro ao extrair PDF: {e}")
        return ""


def _encurtar_url(url: str) -> str:
    try:
        resp = requests.get(f"https://tinyurl.com/api-create.php?url={url}", timeout=5)
        resp.raise_for_status()
        return resp.text.strip()
    except Exception:
        return url


def _get_candidato(candidato_id: int) -> dict | None:
    r = (
        client.table("candidatos")
        .select("*, vagas(titulo)")
        .eq("id", candidato_id)
        .limit(1)
        .execute()
    )
    return r.data[0] if r.data else None


# --- análise de candidatos ---

def _analisar_candidato(c: dict, vaga_titulo: str, descricao: str, openai) -> None:
    """Analisa um candidato via GPT e salva score. Modifica c in-place."""
    texto = c.get("cv_texto") or ""
    if not texto and c.get("cv_url"):
        texto = _extrair_texto_pdf(c["cv_url"])
        if texto:
            client.table("candidatos").update({"cv_texto": texto}).eq("id", c["id"]).execute()

    if not texto:
        client.table("candidatos").update({
            "ranking_score":   0,
            "ranking_analise": "Currículo não disponível para análise.",
            "status":          STATUS_ANALISADO,
        }).eq("id", c["id"]).execute()
        c["ranking_score"]   = 0
        c["ranking_analise"] = "Currículo não disponível para análise."
        return

    prompt = (
        f"Você é um recrutador especialista em estética. Analise o currículo para a vaga de {vaga_titulo}.\n\n"
        f"Vaga: {descricao}\n\n"
        f"Currículo:\n{texto[:4000]}\n\n"
        f'Responda em JSON: {{"nota": 0-10, "analise": "2 linhas: 1 ponto forte e 1 lacuna em relação à vaga"}}'
    )
    try:
        resp    = openai.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        data    = json.loads(resp.choices[0].message.content)
        nota    = float(data.get("nota", 0))
        analise = str(data.get("analise", ""))
    except Exception as e:
        nota, analise = 0.0, f"Erro na análise: {e}"

    client.table("candidatos").update({
        "ranking_score":   nota,
        "ranking_analise": analise,
        "status":          STATUS_ANALISADO,
    }).eq("id", c["id"]).execute()
    c["ranking_score"]   = nota
    c["ranking_analise"] = analise


def analisar_lote_vaga(vaga_id: int) -> None:
    """Analisa todos os candidatos sem score de uma vaga. Chamado em background."""
    try:
        vaga_r = client.table("vagas").select("titulo, descricao, requisitos").eq("id", vaga_id).limit(1).execute()
        if not vaga_r.data:
            return
        v           = vaga_r.data[0]
        vaga_titulo = v["titulo"]
        descricao   = f"{v['descricao']} Requisitos: {v['requisitos']}"

        pendentes = (
            client.table("candidatos")
            .select("id, nome, cv_url, cv_texto")
            .eq("vaga_id", vaga_id)
            .is_("ranking_score", "null")
            .eq("arquivado", False)
            .execute()
        ).data or []

        if not pendentes:
            return

        openai = _get_openai()
        for c in pendentes:
            _analisar_candidato(c, vaga_titulo, descricao, openai)
        log.info(f"Lote analisado: {len(pendentes)} candidato(s) para vaga {vaga_titulo}")
    except Exception as e:
        log.error(f"Erro na análise em lote (vaga_id={vaga_id}): {e}")


# --- tools do agente ---

def ranking_candidatos(vaga: str) -> str:
    vaga_r = (
        client.table("vagas")
        .select("id, titulo, descricao, requisitos")
        .ilike("titulo", f"%{vaga}%")
        .limit(1)
        .execute()
    )
    if not vaga_r.data:
        return f"Vaga '{vaga}' não encontrada. Disponíveis: Consultora, Recepção, Gerente, Esteticista."

    vaga_data   = vaga_r.data[0]
    vaga_id     = vaga_data["id"]
    vaga_titulo = vaga_data["titulo"]
    descricao   = f"{vaga_data['descricao']} Requisitos: {vaga_data['requisitos']}"

    todos = (
        client.table("candidatos")
        .select("id, nome, regiao, cv_url, cv_texto, ranking_score, ranking_analise, status")
        .eq("vaga_id", vaga_id)
        .eq("arquivado", False)
        .order("created_at")
        .execute()
    ).data or []

    if not todos:
        return f"Nenhum candidato inscrito para a vaga {vaga_titulo}."

    openai = _get_openai()
    for c in todos:
        if c.get("ranking_score") is not None:
            continue
        _analisar_candidato(c, vaga_titulo, descricao, openai)

    todos.sort(key=lambda x: x.get("ranking_score") or 0, reverse=True)
    top = todos[:10]

    linhas = [f"Ranking — {vaga_titulo} (top {len(top)} de {len(todos)} candidato(s))\n"]
    for i, c in enumerate(top, 1):
        nota_str = f"{c['ranking_score']:.1f}" if c.get("ranking_score") is not None else "—"
        linhas.append(f"{i}. [ID {c['id']}] {c['nome']} | {c.get('regiao') or '—'} | Nota: {nota_str}")
        if c.get("ranking_analise"):
            linhas.append(f"   {c['ranking_analise']}")
    return "\n".join(linhas)


def contatar_candidato(candidato_id: int) -> str:
    c = _get_candidato(candidato_id)
    if not c:
        return f"Candidato ID {candidato_id} não encontrado."
    if not c.get("telefone"):
        return f"{c['nome']} não tem telefone cadastrado."

    status_anterior = c.get("status") or STATUS_NOVO

    # Marca como contatado atomicamente: só atualiza se ainda não estiver
    update_result = (
        client.table("candidatos")
        .update({"status": STATUS_CONTATADO})
        .eq("id", candidato_id)
        .neq("status", STATUS_CONTATADO)
        .execute()
    )
    if not update_result.data:
        return f"{c['nome']} já foi contatado anteriormente."

    vaga_titulo = (c.get("vagas") or {}).get("titulo") or "nossa vaga"
    link        = LINK_COMPORTAMENTAL or "[link do formulário]"

    mensagem = (
        f"Olá, {c['nome'].split()[0]}!\n\n"
        f"Seu currículo para a vaga de *{vaga_titulo}* foi avaliado com sucesso!\n"
        f"Você foi selecionado(a) para a próxima etapa do processo seletivo.\n\n"
        f"Para continuar, preencha o formulário abaixo — leva menos de 5 minutos:\n{link}\n\n"
        f"Qualquer dúvida, é só chamar aqui."
    )

    try:
        _send(c["telefone"], mensagem)
        return f"Mensagem enviada para {c['nome']} ({c['telefone']}). Status: contatado."
    except Exception as e:
        # Rollback: envio falhou, volta o status anterior
        client.table("candidatos").update({"status": status_anterior}).eq("id", candidato_id).execute()
        log.error(f"Erro ao enviar WhatsApp para {c['nome']} ({c['telefone']}): {e}")
        return f"Erro ao contatar {c['nome']}: {e}"


def encaminhar_franqueado(candidato_id: int) -> str:
    c = _get_candidato(candidato_id)
    if not c:
        return f"Candidato ID {candidato_id} não encontrado."
    if not GRUPO_FRANQUEADOS:
        return "GRUPO_FRANQUEADOS não configurado no .env."

    vaga_titulo = (c.get("vagas") or {}).get("titulo") or "—"
    nota        = c.get("ranking_score")
    nota_str    = f"{nota:.1f}/10" if nota is not None else "—"

    partes = [
        "*Candidato para avaliação*\n",
        f"Nome: {c['nome']}",
        f"Vaga: {vaga_titulo}",
        f"Região: {c.get('regiao') or '—'}",
        f"\n*Compatibilidade: {nota_str}*",
    ]
    if c.get("ranking_analise"):
        partes.append(c["ranking_analise"])
    if c.get("comportamental_perfil"):
        partes.append(f"\n*Perfil comportamental:*\n{c['comportamental_perfil']}")
    if c.get("cv_url"):
        partes.append(f"\nCurrículo: {_encurtar_url(c['cv_url'])}")

    mensagem = "\n".join(partes)

    try:
        _send(GRUPO_FRANQUEADOS, mensagem)
        client.table("candidatos").update({"status": STATUS_ENCAMINHADO}).eq("id", candidato_id).execute()
        return f"{c['nome']} encaminhado para o grupo dos franqueados."
    except Exception as e:
        return f"Erro ao encaminhar {c['nome']}: {e}"


# --- chamado pelo webhook, não pelo agente ---

def processar_comportamental(candidato_id: int, respostas: dict) -> None:
    client.table("candidatos").update({
        "comportamental_respostas": respostas,
        "status":                   STATUS_COMPORTAMENTAL_RECEBIDO,
    }).eq("id", candidato_id).execute()

    vaga_r      = client.table("candidatos").select("vagas(titulo)").eq("id", candidato_id).limit(1).execute()
    vaga_titulo = ((vaga_r.data[0].get("vagas") or {}).get("titulo") or "") if vaga_r.data else ""

    respostas_txt = "\n".join(f"P: {q}\nR: {r}" for q, r in respostas.items())

    prompt = (
        f"Você é especialista em psicologia organizacional. Analise as respostas comportamentais do candidato à vaga de {vaga_titulo}.\n\n"
        f"{respostas_txt}\n\n"
        f"Produza uma análise com:\n"
        f"1. Perfil dominante (ex: Executor, Comunicador, Analista, Planejador)\n"
        f"2. Pontos fortes no contexto profissional (2-3 pontos)\n"
        f"3. Pontos de atenção (1-2 pontos)\n"
        f"4. Fit para a vaga de {vaga_titulo} (alto/médio/baixo + justificativa)\n\n"
        f"Máximo 200 palavras. Seja direto e específico."
    )

    try:
        openai = _get_openai()
        resp   = openai.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
        )
        perfil = resp.choices[0].message.content.strip()
    except Exception as e:
        perfil = f"Erro na análise: {e}"

    client.table("candidatos").update({"comportamental_perfil": perfil}).eq("id", candidato_id).execute()
    log.info(f"Comportamental — análise salva para candidato {candidato_id}")


def arquivar_registro(tipo: str, id: str) -> str:
    tabela = "candidatos" if tipo == "candidato" else "treinamentos"
    r = client.table(tabela).update({"arquivado": True}).eq("id", id).execute()
    if not r.data:
        label = "Candidato" if tipo == "candidato" else "Inscrição"
        return f"{label} ID {id} não encontrado."
    label = "Candidato" if tipo == "candidato" else "Inscrição"
    log.info(f"{label} ID {id} arquivado(a)")
    return f"{label} ID {id} arquivado(a) com sucesso."
