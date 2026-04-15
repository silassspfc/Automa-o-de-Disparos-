import os
import re
from flask import Flask, request, jsonify, render_template
from datetime import datetime, timezone
from services.supabase_client import client
from services.whatsapp import send_reminder, send_report

app = Flask(__name__)

LOCAL_EVENTO = os.getenv("LOCAL_EVENTO", "")


@app.route("/webhook/form", methods=["POST"])
def receive_form():
    """Recebe nova inscrição enviada pelo Power Automate."""
    data = request.json

    nome       = data.get("nome", "").strip()
    unidade    = data.get("unidade", "").strip()
    tel_pessoa = re.sub(r"\D", "", data.get("telefone", ""))  # telefone da pessoa inscrita (guardado, não usado no disparo)
    data_ev    = data.get("data_evento", "").strip()          # formato esperado: YYYY-MM-DD

    if not all([nome, unidade, data_ev]):
        return jsonify({"error": "Campos obrigatórios ausentes: nome, unidade, data_evento"}), 400

    # Busca o telefone do franqueado/gerente responsável pela unidade
    unidade_result = (
        client.table("unidades")
        .select("telefone_responsavel")
        .eq("nome", unidade)
        .limit(1)
        .execute()
    )

    if not unidade_result.data:
        return jsonify({"error": f"Unidade '{unidade}' não cadastrada na base"}), 404

    telefone_responsavel = unidade_result.data[0]["telefone_responsavel"]

    record = client.table("reminders").insert({
        "nome":              nome,
        "telefone":          telefone_responsavel,
        "telefone_inscrito": tel_pessoa,
        "data_evento":       data_ev,
        "unidade":           unidade,
        "local":             LOCAL_EVENTO,
        "status":            "pending"
    }).execute()

    reminder_id = record.data[0]["id"]
    print(f"[FORM] Inscrito salvo: {nome} | unidade {unidade} | responsável {telefone_responsavel} | evento {data_ev} | id {reminder_id}")

    return jsonify({"ok": True, "id": reminder_id}), 200


@app.route("/webhook/whatsapp", methods=["POST"])
def receive_reply():
    """Recebe todas as mensagens do segundo número via Agile Talk."""
    data = request.json

    # Ignora mensagens enviadas pelo próprio bot
    if data.get("method") == "message_sent_waba":
        return jsonify({"ok": True}), 200

    telefone = data.get("ticket", {}).get("contact", {}).get("number", "")
    mensagem = data.get("msg", {}).get("body", "").strip().upper()

    # Aceita variações: SIM, NÃO, NAO, NÃO, sim, não...
    if mensagem not in ("SIM", "NÃO", "NAO"):
        return jsonify({"ok": True}), 200

    status = "confirmed" if mensagem == "SIM" else "declined"

    # Busca o lembrete mais recente desse telefone com status "sent"
    result = (
        client.table("reminders")
        .select("id")
        .eq("telefone", telefone)
        .eq("status", "sent")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )

    if not result.data:
        return jsonify({"ok": True}), 200

    reminder_id = result.data[0]["id"]

    client.table("reminders").update({
        "status":     status,
        "replied_at": datetime.now(timezone.utc).isoformat()
    }).eq("id", reminder_id).execute()

    print(f"[RESPOSTA] Telefone {telefone} respondeu {mensagem} → {status}")
    return jsonify({"ok": True}), 200


@app.route("/disparar", methods=["GET"])
def disparar():
    """Disparo manual: envia WhatsApp para todos os pendentes de uma data de evento."""
    data_ev = request.args.get("data_evento", "").strip()

    if not data_ev:
        return jsonify({"error": "Informe o parâmetro data_evento (ex: /disparar?data_evento=2026-05-10)"}), 400

    result = (
        client.table("reminders")
        .select("*")
        .eq("data_evento", data_ev)
        .eq("status", "pending")
        .execute()
    )

    if not result.data:
        return jsonify({"ok": True, "enviados": 0, "msg": "Nenhum inscrito pendente para essa data"}), 200

    enviados, erros = 0, []

    for reminder in result.data:
        try:
            send_reminder(
                telefone=reminder["telefone"],
                data=reminder["data_evento"],
                unidade=reminder["unidade"],
                local=reminder["local"]
            )
            client.table("reminders").update({
                "status":   "sent",
                "sent_at":  datetime.now(timezone.utc).isoformat()
            }).eq("id", reminder["id"]).execute()
            enviados += 1
            print(f"[DISPARO] Enviado para {reminder['nome']} ({reminder['telefone']})")
        except Exception as e:
            erros.append(reminder["nome"])
            print(f"[ERRO] Falha ao enviar para {reminder['nome']}: {e}")

    return jsonify({"ok": True, "enviados": enviados, "erros": erros}), 200


@app.route("/relatorio", methods=["GET"])
def relatorio():
    """Disparo manual do relatório de confirmações para uma data de evento."""
    data_ev = request.args.get("data_evento", "").strip()

    if not data_ev:
        return jsonify({"error": "Informe o parâmetro data_evento (ex: /relatorio?data_evento=2026-05-10)"}), 400

    result = (
        client.table("reminders")
        .select("*")
        .eq("data_evento", data_ev)
        .execute()
    )

    if not result.data:
        return jsonify({"ok": True, "msg": "Nenhum inscrito encontrado para essa data"}), 200

    # Agrupa por evento (unidade + data)
    eventos = {}
    for reminder in result.data:
        chave = (reminder["unidade"], reminder["data_evento"])
        if chave not in eventos:
            eventos[chave] = {"confirmados": [], "recusados": [], "sem_resposta": [], "ids": []}

        if reminder["status"] == "confirmed":
            eventos[chave]["confirmados"].append(reminder["nome"])
        elif reminder["status"] == "declined":
            eventos[chave]["recusados"].append(reminder["nome"])
        else:
            eventos[chave]["sem_resposta"].append(reminder["nome"])

        eventos[chave]["ids"].append(reminder["id"])

    enviados, erros = 0, []

    for (unidade, data), grupos in eventos.items():
        try:
            send_report(
                unidade=unidade,
                data=data,
                confirmados=grupos["confirmados"],
                recusados=grupos["recusados"],
                sem_resposta=grupos["sem_resposta"]
            )

            for reminder_id in grupos["ids"]:
                client.table("reminders").update({
                    "report_sent": True
                }).eq("id", reminder_id).execute()

            enviados += 1
            print(f"[RELATORIO] Enviado para evento {unidade} em {data}")
        except Exception as e:
            erros.append(unidade)
            print(f"[ERRO] Falha ao enviar relatório para {unidade} em {data}: {e}")

    return jsonify({"ok": True, "relatorios_enviados": enviados, "erros": erros}), 200


@app.route("/painel", methods=["GET"])
def painel():
    """Painel web para disparos manuais."""
    return render_template("painel.html")


@app.route("/preview", methods=["GET"])
def preview():
    """Retorna resumo dos inscritos para uma data: pendentes e respostas por unidade."""
    data_ev = request.args.get("data_evento", "").strip()

    if not data_ev:
        return jsonify({"error": "Informe data_evento"}), 400

    result = (
        client.table("reminders")
        .select("*")
        .eq("data_evento", data_ev)
        .execute()
    )

    pendentes_map = {}
    respostas_map = {}

    for r in (result.data or []):
        unidade = r["unidade"]

        # Pendentes (status = pending)
        if r["status"] == "pending":
            pendentes_map.setdefault(unidade, []).append(r["nome"])

        # Respostas (todos, agrupados por status)
        if unidade not in respostas_map:
            respostas_map[unidade] = {"confirmados": [], "recusados": [], "sem_resposta": []}

        if r["status"] == "confirmed":
            respostas_map[unidade]["confirmados"].append(r["nome"])
        elif r["status"] == "declined":
            respostas_map[unidade]["recusados"].append(r["nome"])
        else:
            respostas_map[unidade]["sem_resposta"].append(r["nome"])

    pendentes = [{"unidade": u, "pessoas": p} for u, p in pendentes_map.items()]
    respostas = [
        {"unidade": u, **v}
        for u, v in respostas_map.items()
    ]

    return jsonify({"pendentes": pendentes, "respostas": respostas})


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
