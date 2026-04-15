import os
import uuid
import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL        = f"https://onochatapi.agiletalk.com.br/v2/api/external/{os.getenv('AGILE_CLIENT_PATH')}"
HEADERS         = {
    "Authorization": f"Bearer {os.getenv('AGILE_BEARERTOKEN')}",
    "Content-Type": "application/json"
}
SECRET_KEY           = os.getenv("AGILE_SECRETKEY")
ORGANIZER_NUMBER     = os.getenv("ORGANIZER_NUMBER")
GESTOR_NUMBER        = os.getenv("GESTOR_NUMBER")
AGENTE_AUTORIZADOS   = set(
    n.strip() for n in os.getenv("AGENTE_NUMEROS_AUTORIZADOS", "").split(",") if n.strip()
)

# --- Edite as mensagens abaixo ---

REMINDER_TEMPLATE = (
    "Unidade: *{unidade}*\n"
    "{lista_nomes}\n\n"
    "Confirma a presença dessas pessoas?\n\n"
    "Responda *SIM* para confirmar ou *NÃO* para cancelar."
)

# Cabeçalho do relatório consolidado (uma única mensagem com todas as unidades)
REPORT_HEADER = "Relatório - {data}\n"

# Linha por unidade quando há confirmações
REPORT_LINHA_CONFIRMADOS = "Unidade: {unidade}\n{total} confirmado(s)"

# Linha por unidade quando ninguém respondeu
REPORT_LINHA_SEM_RESPOSTA = "Unidade: {unidade}\nNão respondeu"

# ---------------------------------


def _send(number: str, body: str):
    payload = {
        "body": body,
        "number": number,
        "externalKey": str(uuid.uuid4()),
        "isClosed": False
    }
    print(f"[API] POST {BASE_URL}")
    print(f"[API] Payload: {payload}")
    response = requests.post(BASE_URL, json=payload, headers=HEADERS, timeout=10)
    print(f"[API] Status: {response.status_code} | Response: {response.text}")
    response.raise_for_status()
    return response.json()


def send_reminder(telefone: str, unidade: str, nomes: list):
    lista = "\n".join(nomes)
    body = REMINDER_TEMPLATE.format(unidade=unidade, lista_nomes=lista)
    return _send(telefone, body)


def send_report(data: str, eventos: dict):
    """
    eventos: {unidade: {"confirmados": [...], "recusados": [...], "sem_resposta": [...]}}
    Envia uma única mensagem consolidada com todas as unidades.
    """
    linhas = [REPORT_HEADER.format(data=data)]

    for unidade, grupos in eventos.items():
        responderam = len(grupos["confirmados"]) + len(grupos["recusados"])
        confirmados = len(grupos["confirmados"])

        if responderam == 0:
            linhas.append(REPORT_LINHA_SEM_RESPOSTA.format(unidade=unidade))
        else:
            linhas.append(REPORT_LINHA_CONFIRMADOS.format(unidade=unidade, total=confirmados))

    body = "\n\n".join(linhas)
    return _send(ORGANIZER_NUMBER, body)
