import os
import json
from datetime import date
from openai import OpenAI
from services.supabase_client import client as supabase
from services.treinamentos import preview_confirmacao, confirmar_presenca, relatorio_confirmacoes, ativar_treinamento


def _get_openai_client():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY não configurada.")
    return OpenAI(api_key=api_key)


SYSTEM_PROMPT = """Você é um assistente de gestão da Onodera Estética, especialista em controle de treinamentos.
Responda sempre em português, de forma direta e concisa, sem formatação markdown.
Hoje é {today}.

Você SEMPRE deve usar uma das ferramentas disponíveis para responder — nunca responda diretamente sem usar uma ferramenta.
Para respostas de texto simples, use a ferramenta "responder".

Fluxo obrigatório para confirmação de presença:
1. Quando o gestor pedir para confirmar presença ou entrar em contato com as unidades → use PRIMEIRO preview_confirmacao_treinamento para mostrar quem vai receber.
2. Somente quando o gestor disser "pode enviar", "confirma", "sim" ou similar após o preview → use confirmar_presenca_treinamento para disparar as mensagens."""

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
            "description": "Lista todos os treinamentos agendados no cronograma com data, tipo e público.",
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
            "description": "Envia mensagem de WhatsApp para os responsáveis de cada unidade perguntando se os inscritos confirmarão presença. Usar quando o gestor pedir para confirmar presença, avisar responsáveis, entrar em contato com as unidades, ou qualquer variação desse pedido.",
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
            "description": "Envia mensagem de ativação para o grupo geral do WhatsApp divulgando o treinamento e o link de inscrição. Usar quando o gestor pedir para ativar, divulgar ou disparar o treinamento.",
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
]


def _listar_treinamentos() -> str:
    result = (
        supabase.table("cronograma")
        .select("data, treinamento, tipo, publico")
        .order("data")
        .execute()
    )
    if not result.data:
        return "Nenhum treinamento agendado."
    linhas = [
        f"{r['data']} — {r['treinamento']} ({r['tipo']}, {r['publico']})"
        for r in result.data
    ]
    return "\n".join(linhas)


def _buscar_inscritos(data: str) -> str:
    result = (
        supabase.table("treinamentos")
        .select("nome, unidade, treinamento")
        .eq("data_treinamento", data)
        .execute()
    )
    registros = result.data or []
    if not registros:
        return f"Nenhum inscrito para {data}."

    grupos = {}
    for r in registros:
        grupos.setdefault(r["treinamento"], []).append(f"{r['unidade']} - {r['nome']}")

    linhas = []
    for tr, pessoas in grupos.items():
        linhas.append(f"{tr}:\n" + "\n".join(f"  {p}" for p in pessoas))

    return f"{len(registros)} inscrito(s) em {data}:\n\n" + "\n\n".join(linhas)


def _buscar_medicos(data: str) -> str:
    result = (
        supabase.table("treinamentos")
        .select("nome, unidade, crm, treinamento")
        .eq("data_treinamento", data)
        .execute()
    )
    medicos = [r for r in (result.data or []) if r.get("crm")]
    if not medicos:
        return f"Nenhum médico inscrito para {data}."
    linhas = [f"{r['unidade']}, {r['nome']}, CRM: {r['crm']}" for r in medicos]
    return f"{len(medicos)} médico(s) inscrito(s):\n" + "\n".join(linhas)


def _execute_tool(name: str, args: dict) -> str | None:
    if name == "responder":
        return None  # sinaliza que a resposta já está em args["mensagem"]
    if name == "listar_treinamentos":
        return _listar_treinamentos()
    if name == "buscar_inscritos_por_data":
        return _buscar_inscritos(args["data"])
    if name == "buscar_medicos_por_data":
        return _buscar_medicos(args["data"])
    if name == "preview_confirmacao_treinamento":
        return preview_confirmacao(args["data"])
    if name == "confirmar_presenca_treinamento":
        return confirmar_presenca(args["data"])
    if name == "ativar_treinamento":
        return ativar_treinamento(args["data"])
    if name == "relatorio_confirmacoes_treinamento":
        return relatorio_confirmacoes(args["data"])
    return "Ferramenta desconhecida."


# Tools que retornam dados formatados para exibição — resultado vai direto ao usuário
_DISPLAY_TOOLS = {
    "preview_confirmacao_treinamento",
    "buscar_inscritos_por_data",
    "buscar_medicos_por_data",
    "listar_treinamentos",
    "relatorio_confirmacoes_treinamento",
    "ativar_treinamento",
}


def process_gestor_message(mensagem: str) -> str:
    today  = date.today().strftime("%d/%m/%Y")
    client = _get_openai_client()

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.format(today=today)},
        {"role": "user",   "content": mensagem}
    ]

    for _ in range(5):
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            tools=TOOLS,
            tool_choice="required"
        )

        msg  = response.choices[0].message
        tc   = msg.tool_calls[0]
        args = json.loads(tc.function.arguments)

        print(f"[AGENTE] Tool chamada: {tc.function.name} | args: {args}")

        if tc.function.name == "responder":
            return args["mensagem"]

        result = _execute_tool(tc.function.name, args)

        # Dados de consulta: retorna direto sem passar pelo LLM de novo
        if tc.function.name in _DISPLAY_TOOLS:
            return result

        messages.append(msg)
        messages.append({
            "role":         "tool",
            "tool_call_id": tc.id,
            "content":      result
        })

    return "Não consegui processar sua solicitação."
