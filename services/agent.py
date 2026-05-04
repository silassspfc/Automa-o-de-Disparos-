import os
import json
import logging
from datetime import date
from openai import OpenAI

log = logging.getLogger(__name__)
from services.memoria import carregar_historico, salvar_historico
from services.treinamentos import (
    listar_treinamentos,
    buscar_inscritos,
    buscar_medicos,
    preview_confirmacao,
    confirmar_presenca,
    relatorio_confirmacoes,
    preview_ativacao,
    ativar_treinamento,
)
from services.recrutamento import (
    ranking_candidatos,
    contatar_candidato,
    encaminhar_franqueado,
    arquivar_registro,
    reativar_registro,
)
from services.constants import OPENAI_MODEL


def _get_openai_client():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY não configurada.")
    return OpenAI(api_key=api_key)


SYSTEM_PROMPT = """Você é um assistente de gestão da Onodera Estética, especialista em treinamentos e recrutamento.
Responda sempre em português, de forma direta e concisa, sem formatação markdown.
Hoje é {today}.

Você SEMPRE deve usar uma das ferramentas disponíveis para responder — nunca responda diretamente sem usar uma ferramenta.
Para respostas de texto simples, use a ferramenta "responder".

Fluxo obrigatório para confirmação de presença:
1. Quando o gestor pedir para confirmar presença ou entrar em contato com as unidades → use PRIMEIRO preview_confirmacao_treinamento para mostrar quem vai receber.
2. Somente quando o gestor disser "pode enviar", "confirma", "sim" ou similar após o preview → use confirmar_presenca_treinamento para disparar as mensagens.

Fluxo obrigatório para ativação de treinamento:
1. Quando o gestor pedir para ativar ou divulgar um treinamento → use PRIMEIRO preview_ativacao_treinamento para mostrar a mensagem que será enviada ao grupo.
2. Somente quando o gestor disser "pode enviar", "confirma", "sim" ou similar após o preview → use ativar_treinamento para disparar.

Fluxo obrigatório para recrutamento:
1. Quando o gestor pedir ranking ou análise de candidatos para uma vaga → use ranking_candidatos.
2. Quando o gestor validar o ranking e pedir para contatar um candidato → use contatar_candidato com o ID informado.
3. Quando o gestor autorizar o encaminhamento de um candidato para os franqueados → use encaminhar_franqueado com o ID informado.

Arquivamento e reativação:
- Para arquivar candidatos ou inscrições → use arquivar_registro com o nome da pessoa.
- Para desfazer um arquivamento → use reativar_registro com o nome da pessoa."""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "responder",
            "description": "Envia uma resposta de texto ao gestor. Usar para perguntas gerais, pedidos de esclarecimento ou quando nenhuma outra ferramenta se aplica.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mensagem": {"type": "string", "description": "Texto da resposta ao gestor"}
                },
                "required": ["mensagem"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "listar_treinamentos",
            "description": "Lista os próximos treinamentos agendados a partir de hoje. Usar quando o gestor perguntar sobre próximos treinamentos, o que tem agendado, cronograma, etc.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_inscritos_por_data",
            "description": "Busca todos os inscritos em treinamentos para uma data específica, agrupados por treinamento.",
            "parameters": {
                "type": "object",
                "properties": {
                    "data": {"type": "string", "description": "Data no formato YYYY-MM-DD, ex: 2026-05-07"}
                },
                "required": ["data"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_medicos_por_data",
            "description": "Busca médicos (com CRM) inscritos em treinamentos para uma data específica.",
            "parameters": {
                "type": "object",
                "properties": {
                    "data": {"type": "string", "description": "Data no formato YYYY-MM-DD, ex: 2026-05-07"}
                },
                "required": ["data"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "preview_confirmacao_treinamento",
            "description": "Mostra ao gestor quais unidades e inscritos receberão a mensagem de confirmação, sem enviar nada. Usar como primeiro passo sempre que o gestor pedir para confirmar presença ou entrar em contato com as unidades.",
            "parameters": {
                "type": "object",
                "properties": {
                    "data": {"type": "string", "description": "Data no formato YYYY-MM-DD, ex: 2026-05-15"}
                },
                "required": ["data"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "confirmar_presenca_treinamento",
            "description": "Envia mensagem de WhatsApp para os responsáveis de cada unidade perguntando se os inscritos confirmarão presença. Usar SOMENTE após o gestor confirmar o preview com 'pode enviar' ou similar.",
            "parameters": {
                "type": "object",
                "properties": {
                    "data": {"type": "string", "description": "Data no formato YYYY-MM-DD, ex: 2026-05-15"}
                },
                "required": ["data"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "preview_ativacao_treinamento",
            "description": "Mostra ao gestor a mensagem que será enviada ao grupo de WhatsApp, sem enviar. Usar como primeiro passo quando o gestor pedir para ativar ou divulgar um treinamento.",
            "parameters": {
                "type": "object",
                "properties": {
                    "data": {"type": "string", "description": "Data no formato YYYY-MM-DD, ex: 2026-05-15"}
                },
                "required": ["data"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ativar_treinamento",
            "description": "Envia mensagem de ativação para o grupo geral do WhatsApp. Usar SOMENTE após o gestor confirmar o preview com 'pode enviar' ou similar.",
            "parameters": {
                "type": "object",
                "properties": {
                    "data": {"type": "string", "description": "Data no formato YYYY-MM-DD, ex: 2026-05-15"}
                },
                "required": ["data"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "relatorio_confirmacoes_treinamento",
            "description": "Retorna relatório de confirmados, recusados e sem resposta para os treinamentos presenciais de uma data.",
            "parameters": {
                "type": "object",
                "properties": {
                    "data": {"type": "string", "description": "Data no formato YYYY-MM-DD, ex: 2026-05-15"}
                },
                "required": ["data"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ranking_candidatos",
            "description": "Analisa os currículos dos candidatos inscritos para uma vaga e retorna um ranking com nota e justificativa para cada um.",
            "parameters": {
                "type": "object",
                "properties": {
                    "vaga": {"type": "string", "description": "Nome da vaga: Consultora, Recepção, Gerente ou Esteticista"}
                },
                "required": ["vaga"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "contatar_candidato",
            "description": "Envia WhatsApp para o candidato informando que foi aprovado para a próxima etapa e inclui o link do formulário comportamental. Usar após o gestor validar o ranking.",
            "parameters": {
                "type": "object",
                "properties": {
                    "candidato_id": {"type": "integer", "description": "ID do candidato retornado pelo ranking"}
                },
                "required": ["candidato_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "arquivar_registro",
            "description": "Arquiva (soft delete) candidatos ou inscrições de treinamento pelo nome. O registro fica invisível mas não é apagado do banco. Usar quando o gestor pedir para remover, arquivar ou excluir.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tipo": {
                        "type": "string",
                        "enum": ["candidato", "inscricao"],
                        "description": "'candidato' para arquivar da tabela de candidatos, 'inscricao' para arquivar inscrições de treinamento"
                    },
                    "nome": {"type": "string", "description": "Nome (ou parte do nome) da pessoa a arquivar"}
                },
                "required": ["tipo", "nome"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "reativar_registro",
            "description": "Reativa um candidato ou inscrição que foi arquivada por engano. Usar quando o gestor pedir para desfazer um arquivamento ou restaurar um registro.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tipo": {
                        "type": "string",
                        "enum": ["candidato", "inscricao"],
                        "description": "'candidato' para reativar da tabela de candidatos, 'inscricao' para reativar inscrições de treinamento"
                    },
                    "nome": {"type": "string", "description": "Nome (ou parte do nome) da pessoa a reativar"}
                },
                "required": ["tipo", "nome"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "encaminhar_franqueado",
            "description": "Envia para o grupo dos franqueados o perfil completo do candidato: nota de compatibilidade, análise do currículo, perfil comportamental e link do PDF. Usar somente após autorização do gestor.",
            "parameters": {
                "type": "object",
                "properties": {
                    "candidato_id": {"type": "integer", "description": "ID do candidato a encaminhar"}
                },
                "required": ["candidato_id"]
            }
        }
    },
]

# Tools cujo resultado vai direto ao gestor sem passar pelo LLM de novo
_DISPLAY_TOOLS = {
    "listar_treinamentos",
    "buscar_inscritos_por_data",
    "buscar_medicos_por_data",
    "preview_confirmacao_treinamento",
    "confirmar_presenca_treinamento",
    "preview_ativacao_treinamento",
    "ativar_treinamento",
    "relatorio_confirmacoes_treinamento",
    "ranking_candidatos",
    "contatar_candidato",
    "encaminhar_franqueado",
    "arquivar_registro",
    "reativar_registro",
}

# Mapeamento tool_name → handler; adicionar uma tool nova = uma linha aqui
_TOOL_HANDLERS = {
    "listar_treinamentos":                lambda a: listar_treinamentos(),
    "buscar_inscritos_por_data":          lambda a: buscar_inscritos(a["data"]),
    "buscar_medicos_por_data":            lambda a: buscar_medicos(a["data"]),
    "preview_confirmacao_treinamento":    lambda a: preview_confirmacao(a["data"]),
    "confirmar_presenca_treinamento":     lambda a: confirmar_presenca(a["data"]),
    "preview_ativacao_treinamento":       lambda a: preview_ativacao(a["data"]),
    "ativar_treinamento":                 lambda a: ativar_treinamento(a["data"]),
    "relatorio_confirmacoes_treinamento": lambda a: relatorio_confirmacoes(a["data"]),
    "ranking_candidatos":                 lambda a: ranking_candidatos(a["vaga"]),
    "contatar_candidato":                 lambda a: contatar_candidato(int(a["candidato_id"])),
    "encaminhar_franqueado":              lambda a: encaminhar_franqueado(int(a["candidato_id"])),
    "arquivar_registro":                  lambda a: arquivar_registro(a["tipo"], a["nome"]),
    "reativar_registro":                  lambda a: reativar_registro(a["tipo"], a["nome"]),
}


def _execute_tool(name: str, args: dict) -> str | None:
    if name == "responder":
        return None
    handler = _TOOL_HANDLERS.get(name)
    return handler(args) if handler else "Ferramenta desconhecida."


def process_gestor_message(mensagem: str) -> str:
    today  = date.today().strftime("%d/%m/%Y")
    openai = _get_openai_client()

    historico = carregar_historico()

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.format(today=today)},
        *historico,
        {"role": "user", "content": mensagem}
    ]

    salvar_historico("user", mensagem)

    for _ in range(5):
        response = openai.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            tools=TOOLS,
            tool_choice="required"
        )

        msg = response.choices[0].message
        if not msg.tool_calls:
            return "Não consegui processar sua solicitação."
        tc   = msg.tool_calls[0]
        args = json.loads(tc.function.arguments)

        log.info(f"Tool chamada: {tc.function.name} | args: {args}")

        if tc.function.name == "responder":
            salvar_historico("assistant", args["mensagem"])
            return args["mensagem"]

        result = _execute_tool(tc.function.name, args)

        if tc.function.name in _DISPLAY_TOOLS:
            salvar_historico("assistant", result)
            return result

        messages.append(msg)
        messages.append({
            "role":         "tool",
            "tool_call_id": tc.id,
            "content":      result
        })

    return "Não consegui processar sua solicitação."
