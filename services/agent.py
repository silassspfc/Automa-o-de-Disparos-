import os
import json
from datetime import date
from openai import OpenAI
from services.supabase_client import client as supabase
from services.certificados import gerar_e_enviar_certificados

def _get_openai_client():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY não configurada.")
    return OpenAI(api_key=api_key)

SYSTEM_PROMPT = """Você é um assistente de gestão da Onodera Estética.
Responda sempre em português, de forma direta e concisa, sem formatação markdown.
Hoje é {today}.
Você tem acesso a ferramentas para consultar inscrições de treinamentos no banco de dados."""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "buscar_medicos_por_data",
            "description": "Busca médicos (com CRM) inscritos em treinamentos para uma data específica.",
            "parameters": {
                "type": "object",
                "properties": {
                    "data": {
                        "type": "string",
                        "description": "Data no formato YYYY-MM-DD, ex: 2026-05-07"
                    }
                },
                "required": ["data"]
            }
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
                    "data": {
                        "type": "string",
                        "description": "Data no formato YYYY-MM-DD, ex: 2026-05-07"
                    }
                },
                "required": ["data"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "listar_treinamentos",
            "description": "Lista todos os treinamentos agendados no cronograma.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "gerar_certificados_por_data",
            "description": "Gera e envia por email os certificados de todos os inscritos de uma data de treinamento. Salva os arquivos no Supabase Storage.",
            "parameters": {
                "type": "object",
                "properties": {
                    "data": {
                        "type": "string",
                        "description": "Data no formato YYYY-MM-DD, ex: 2026-05-04"
                    }
                },
                "required": ["data"]
            }
        }
    }
]


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
        tr = r["treinamento"]
        grupos.setdefault(tr, []).append(f"{r['unidade']} - {r['nome']}")

    linhas = []
    for tr, pessoas in grupos.items():
        linhas.append(f"{tr}:\n" + "\n".join(f"  {p}" for p in pessoas))

    return f"{len(registros)} inscrito(s) em {data}:\n\n" + "\n\n".join(linhas)


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


def _execute_tool(name: str, args: dict) -> str:
    if name == "buscar_medicos_por_data":
        return _buscar_medicos(args["data"])
    if name == "buscar_inscritos_por_data":
        return _buscar_inscritos(args["data"])
    if name == "listar_treinamentos":
        return _listar_treinamentos()
    if name == "gerar_certificados_por_data":
        return gerar_e_enviar_certificados(args["data"])
    return "Ferramenta desconhecida."


def process_gestor_message(mensagem: str) -> str:
    """Recebe mensagem do gestor, chama OpenAI com tools e retorna resposta em texto."""
    today = date.today().strftime("%d/%m/%Y")

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.format(today=today)},
        {"role": "user",   "content": mensagem}
    ]

    for _ in range(5):
        response = _get_openai_client().chat.completions.create(
            model="gpt-4o-mini",
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
