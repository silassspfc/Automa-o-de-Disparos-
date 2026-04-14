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
SECRET_KEY      = os.getenv("AGILE_SECRETKEY")
ORGANIZER_NUMBER = os.getenv("ORGANIZER_NUMBER")

REMINDER_TEMPLATE = (
    "Olá!\n\n"
    "O evento da unidade *{unidade}* está confirmado para o dia *{data}*, "
    "em *{local}*.\n\n"
    "A equipe da sua unidade irá comparecer?\n\n"
    "Responda *SIM* para confirmar ou *NÃO* para cancelar."
)

REPORT_TEMPLATE = (
    "Resumo do evento - *{unidade}* - {data}\n\n"
    "Confirmados ({total_confirmados}):\n{lista_confirmados}\n\n"
    "Recusados ({total_recusados}):\n{lista_recusados}\n\n"
    "Sem resposta ({total_sem_resposta}):\n{lista_sem_resposta}"
)


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


def send_reminder(telefone: str, data: str, unidade: str, local: str):
    body = REMINDER_TEMPLATE.format(
        data=data,
        unidade=unidade,
        local=local
    )
    return _send(telefone, body)


def send_report(unidade: str, data: str, confirmados: list, recusados: list, sem_resposta: list):
    def format_list(pessoas):
        if not pessoas:
            return "  (nenhum)"
        return "\n".join(f"  - {p}" for p in pessoas)

    body = REPORT_TEMPLATE.format(
        unidade=unidade,
        data=data,
        total_confirmados=len(confirmados),
        lista_confirmados=format_list(confirmados),
        total_recusados=len(recusados),
        lista_recusados=format_list(recusados),
        total_sem_resposta=len(sem_resposta),
        lista_sem_resposta=format_list(sem_resposta)
    )
    return _send(ORGANIZER_NUMBER, body)
