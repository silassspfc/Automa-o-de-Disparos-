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
    """Recebe nova inscrição — suporta Tally e payload flat."""
    payload = request.json
    print(f"[FORM] Payload recebido: {payload}")

    # Formato Tally
    if "data" in payload and "fields" in payload.get("data", {}):
        def achar(keyword):
            for f in payload["data"]["fields"]:
                if keyword.lower() in f["label"].lower():
                    if f.get("type") == "DROPDOWN" and isinstance(f.get("value"), list):
                        selected = [o["text"] for o in f.get("options", []) if o["id"] in f["value"]]
                        return selected[0] if selected else ""
                    return str(f["value"]).strip()
            return ""

        nome       = achar("nome")
        unidade    = achar("unidade")
        tel_pessoa = re.sub(r"\D", "", achar("telefone"))
        data_ev    = achar("data")
    else:
        # Formato flat (Power Automate)
        nome       = payload.get("nome", "").strip()
        unidade    = payload.get("unidade", "").strip()
        tel_pessoa = re.sub(r"\D", "", payload.get("telefone", ""))
        data_ev    = payload.get("data_evento", "").strip()

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

    # Agrupa por (unidade, telefone) → 1 mensagem por unidade com todos os nomes
    grupos = {}
    for r in result.data:
        chave = (r["unidade"], r["telefone"])
        if chave not in grupos:
            grupos[chave] = {"nomes": [], "ids": []}
        grupos[chave]["nomes"].append(r["nome"])
        grupos[chave]["ids"].append(r["id"])

    enviados, erros = 0, []

    for (unidade, telefone), dados in grupos.items():
        try:
            send_reminder(telefone=telefone, unidade=unidade, nomes=dados["nomes"])
            for rid in dados["ids"]:
                client.table("reminders").update({
                    "status":  "sent",
                    "sent_at": datetime.now(timezone.utc).isoformat()
                }).eq("id", rid).execute()
            enviados += len(dados["nomes"])
            print(f"[DISPARO] Enviado para unidade {unidade} ({telefone}) — {len(dados['nomes'])} pessoa(s)")
        except Exception as e:
            erros.extend(dados["nomes"])
            print(f"[ERRO] Falha ao enviar para {unidade}: {e}")

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

    # Agrupa todas as unidades em um único dict para envio consolidado
    eventos = {}
    ids = []
    for r in result.data:
        unidade = r["unidade"]
        if unidade not in eventos:
            eventos[unidade] = {"confirmados": [], "recusados": [], "sem_resposta": []}
        if r["status"] == "confirmed":
            eventos[unidade]["confirmados"].append(r["nome"])
        elif r["status"] == "declined":
            eventos[unidade]["recusados"].append(r["nome"])
        else:
            eventos[unidade]["sem_resposta"].append(r["nome"])
        ids.append(r["id"])

    try:
        send_report(data=data_ev, eventos=eventos)
        for rid in ids:
            client.table("reminders").update({"report_sent": True}).eq("id", rid).execute()
        print(f"[RELATORIO] Enviado para evento {data_ev} — {len(eventos)} unidade(s)")
    except Exception as e:
        print(f"[ERRO] Falha ao enviar relatório: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

    return jsonify({"ok": True, "relatorios_enviados": 1, "erros": []}), 200


@app.route("/webhook/treinamento", methods=["POST"])
def receive_treinamento():
    """Recebe nova presença em treinamento — suporta Tally e payload flat."""
    payload = request.json
    print(f"[TREINAMENTO] Payload recebido: {payload}")

    # Formato Tally: { "data": { "fields": [ { "label": "...", "value": "..." } ] } }
    if "data" in payload and "fields" in payload.get("data", {}):
        # Busca por palavra-chave no label (case-insensitive) — robusto a variações de texto
        def achar(keyword):
            for f in payload["data"]["fields"]:
                if keyword.lower() in f["label"].lower():
                    if f.get("type") == "DROPDOWN" and isinstance(f.get("value"), list):
                        selected = [o["text"] for o in f.get("options", []) if o["id"] in f["value"]]
                        return selected[0] if selected else ""
                    return str(f["value"]).strip()
            return ""

        nome        = achar("nome")
        email       = achar("email")
        treinamento = achar("treinamento")
        data_tr     = achar("data")
        unidade     = achar("unidade")
    else:
        # Formato flat (Power Automate)
        nome        = payload.get("nome", "").strip()
        email       = payload.get("email", "").strip()
        treinamento = payload.get("treinamento", "").strip()
        data_tr     = payload.get("data_treinamento", "").strip()
        unidade     = payload.get("unidade", "").strip()

    if not all([nome, email, treinamento, data_tr]):
        print(f"[TREINAMENTO] Campos ausentes — nome={nome} email={email} treinamento={treinamento} data={data_tr}")
        return jsonify({"error": "Campos obrigatórios ausentes: nome, email, treinamento, data_treinamento"}), 400

    # Aceita dd/MM/yyyy ou yyyy-MM-dd
    if len(data_tr) == 10 and data_tr[2] == "/":
        from datetime import datetime as dt
        data_tr = dt.strptime(data_tr, "%d/%m/%Y").strftime("%Y-%m-%d")

    record = client.table("treinamentos").insert({
        "nome":             nome,
        "email":            email,
        "treinamento":      treinamento,
        "data_treinamento": data_tr,
        "unidade":          unidade,
    }).execute()

    registro_id = record.data[0]["id"]
    print(f"[TREINAMENTO] Salvo: {nome} | {treinamento} | {data_tr} | id {registro_id}")

    return jsonify({"ok": True, "id": registro_id}), 200


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
