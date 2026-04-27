import os
import json
from datetime import date
from openai import OpenAI
from services.supabase_client import client as supabase
from services.treinamentos import confirmar_presenca, relatorio_confirmacoes


def _get_openai_client():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY não configurada.")
    return OpenAI(api_key=api_key)


SYSTEM_PROMPT = """Você é um assistente de gestão da Onodera Estética, especialista em controle de treinamentos.
Responda sempre em português, de forma direta e concisa, sem formatação markdown.
Hoje é {today}.

Você TEM a capacidade de enviar mensagens de WhatsApp para os responsáveis das unidades através das ferramentas disponíveis. NUNCA diga que não pode entrar em contato com responsáveis.

Regras obrigatórias de uso das ferramentas:
- "confirmar presença", "entrar em contato com responsáveis", "enviar confirmação", "confirmar treinamento" → chame IMEDIATAMENTE confirmar_presenca_treinamento. Se o gestor não informou a data, pergunte apenas a data.
- "quem confirmou", "relatório de confirmações", "quem respondeu", "status das confirmações" → use relatorio_confirmacoes_treinamento.
- "quem está inscrito", "inscritos na data" → use buscar_inscritos_por_data.
- "ver treinamentos", "cronograma" → use listar_treinamentos."""

TOOLS = [
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
            "name": "confirmar_presenca_treinamento",
            "description": "Envia mensagem de WhatsApp para os responsáveis de cada unidade perguntando se os inscritos confirmarão presença no treinamento presencial de uma data. Acionar quando o gestor pedir para 'confirmar presença', 'enviar confirmação', 'confirmar treinamento' ou expressões similares.",
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


def _execute_tool(name: str, args: dict) -> str:
    if name == "listar_treinamentos":
        return _listar_treinamentos()
    if name == "buscar_inscritos_por_data":
        return _buscar_inscritos(args["data"])
    if name == "buscar_medicos_por_data":
        return _buscar_medicos(args["data"])
    if name == "confirmar_presenca_treinamento":
        return confirmar_presenca(args["data"])
    if name == "relatorio_confirmacoes_treinamento":
        return relatorio_confirmacoes(args["data"])
    return "Ferramenta desconhecida."


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
            tool_choice="auto"
        )

        msg = response.choices[0].message

        if not msg.tool_calls:
            return msg.content

        messages.append(msg)
        for tc in msg.tool_calls:
            args   = json.loads(tc.function.arguments)
            result = _execute_tool(tc.function.name, args)
            messages.append({
                "role":         "tool",
                "tool_call_id": tc.id,
                "content":      result
            })

    return "Não consegui processar sua solicitação."
