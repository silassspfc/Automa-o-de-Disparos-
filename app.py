import os
import re
from flask import Flask, request, jsonify, render_template
from datetime import datetime, timezone
from services.supabase_client import client
from services.whatsapp import send_reminder, send_report, GESTOR_NUMBER, AGENTE_AUTORIZADOS, _send
from services.agent import process_gestor_message
from services.tally import achar

app = Flask(__name__)

LOCAL_EVENTO = os.getenv("LOCAL_EVENTO", "")

TRAINING_LABELS = ("online", "prescencial", "presencial")


@app.route("/webhook/form", methods=["POST"])
def receive_form():
    """Recebe nova inscrição via Tally ou payload flat."""
    payload = request.json
    print(f"[FORM] Payload recebido: {payload}")

    if "data" in payload and "fields" in payload.get("data", {}):
        fields     = payload["data"]["fields"]
        nome       = achar(fields, "nome")
        unidade    = achar(fields, "unidade")
        tel_pessoa = re.sub(r"\D", "", achar(fields, "telefone"))
        data_ev    = achar(fields, "data")
    else:
        nome       = payload.get("nome", "").strip()
        unidade    = payload.get("unidade", "").strip()
        tel_pessoa = re.sub(r"\D", "", payload.get("telefone", ""))
        data_ev    = payload.get("data_evento", "").strip()

    if not all([nome, unidade, data_ev]):
        return jsonify({"error": "Campos obrigatórios ausentes: nome, unidade, data_evento"}), 400

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
    """Recebe mensagens do número bot via Agile Talk."""
    data = request.json

    if data.get("method") == "message_sent_waba":
        return jsonify({"ok": True}), 200

    telefone = data.get("ticket", {}).get("contact", {}).get("number", "")
    mensagem = (data.get("msg", {}).get("body") or "").strip()

    if not telefone:
        print(f"[WHATSAPP] Payload sem telefone ignorado: {data}")
        return jsonify({"ok": True}), 200

    if telefone in AGENTE_AUTORIZADOS or (GESTOR_NUMBER and telefone == GESTOR_NUMBER):
        print(f"[GESTOR] Mensagem recebida de {telefone}: {mensagem}")
        try:
            resposta = process_gestor_message(mensagem)
            _send(GESTOR_NUMBER, resposta)
            print(f"[GESTOR] Resposta enviada: {resposta}")
        except Exception as e:
            print(f"[GESTOR] Erro ao processar: {e}")
        return jsonify({"ok": True}), 200

    mensagem_upper = mensagem.upper()
    if mensagem_upper not in ("SIM", "NÃO", "NAO"):
        return jsonify({"ok": True}), 200

    status = "confirmed" if mensagem_upper == "SIM" else "declined"

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

    print(f"[RESPOSTA] {telefone} respondeu {mensagem_upper} → {status}")
    return jsonify({"ok": True}), 200


@app.route("/disparar", methods=["GET"])
def disparar():
    """Envia WhatsApp para todos os inscritos pendentes de uma data de evento."""
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
            print(f"[DISPARO] Unidade {unidade} ({telefone}) — {len(dados['nomes'])} pessoa(s)")
        except Exception as e:
            erros.extend(dados["nomes"])
            print(f"[ERRO] Falha ao enviar para {unidade}: {e}")

    return jsonify({"ok": True, "enviados": enviados, "erros": erros}), 200


@app.route("/relatorio", methods=["GET"])
def relatorio():
    """Envia relatório de confirmações para uma data de evento."""
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
        print(f"[RELATORIO] Evento {data_ev} — {len(eventos)} unidade(s)")
    except Exception as e:
        print(f"[ERRO] Falha ao enviar relatório: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

    return jsonify({"ok": True, "relatorios_enviados": 1, "erros": []}), 200


@app.route("/webhook/treinamento", methods=["POST"])
def receive_treinamento():
    """Recebe inscrição de treinamento via Tally ou payload flat."""
    payload = request.json
    print(f"[TREINAMENTO] Payload recebido: {payload}")

    if "data" in payload and "fields" in payload.get("data", {}):
        fields  = payload["data"]["fields"]
        nome    = achar(fields, "nome",    exclude_parens=True)
        unidade = achar(fields, "unidade", exclude_parens=True)
        email   = achar(fields, "email",   exclude_parens=True)
        crm     = achar(fields, "crm",     exclude_parens=True)

        treinamentos_selecionados = []
        for f in fields:
            label_lower = f["label"].lower().strip()
            tipo  = f.get("type", "")
            valor = f.get("value")

            if tipo == "CHECKBOXES" and isinstance(valor, list) and "(" not in f["label"]:
                if any(label_lower == t or label_lower.startswith(t) for t in TRAINING_LABELS):
                    selected = [o["text"] for o in f.get("options", []) if o["id"] in valor]
                    treinamentos_selecionados.extend(selected)

            elif tipo == "HIDDEN_FIELDS" and f["label"].strip():
                treinamentos_selecionados.append(f["label"].strip())
    else:
        nome    = payload.get("nome", "").strip()
        email   = payload.get("email", "").strip()
        crm     = payload.get("crm", "").strip()
        unidade = payload.get("unidade", "").strip()
        tr      = payload.get("treinamento", "").strip()
        treinamentos_selecionados = [tr] if tr else []

    if not treinamentos_selecionados:
        form_id = payload.get("data", {}).get("formId", "")
        if form_id:
            cron_form = (
                client.table("cronograma")
                .select("treinamento")
                .eq("tally_form_id", form_id)
                .limit(1)
                .execute()
            )
            if cron_form.data:
                treinamentos_selecionados = [cron_form.data[0]["treinamento"]]
                print(f"[TREINAMENTO] Treinamento encontrado pelo formId {form_id}: {treinamentos_selecionados[0]}")

    if not nome or not treinamentos_selecionados:
        print(f"[TREINAMENTO] Campos ausentes — nome={nome} treinamentos={treinamentos_selecionados}")
        return jsonify({"error": "Campos obrigatórios ausentes: nome, treinamento"}), 400

    ids_salvos = []
    for treinamento in treinamentos_selecionados:
        cron = (
            client.table("cronograma")
            .select("data")
            .eq("treinamento", treinamento)
            .limit(1)
            .execute()
        )
        data_tr = cron.data[0]["data"] if cron.data else None
        print(f"[TREINAMENTO] Data encontrada: {data_tr} para '{treinamento}'")

        record = client.table("treinamentos").insert({
            "nome":             nome,
            "email":            email or None,
            "crm":              crm or None,
            "treinamento":      treinamento,
            "data_treinamento": data_tr,
            "unidade":          unidade,
        }).execute()

        ids_salvos.append(record.data[0]["id"])
        print(f"[TREINAMENTO] Salvo: {nome} | {treinamento} | {data_tr} | id {record.data[0]['id']}")

    return jsonify({"ok": True, "ids": ids_salvos}), 200


@app.route("/painel", methods=["GET"])
def painel():
    return render_template("painel.html")


@app.route("/preview", methods=["GET"])
def preview():
    """Retorna inscritos de uma data agrupados por status e unidade."""
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

        if r["status"] == "pending":
            pendentes_map.setdefault(unidade, []).append(r["nome"])

        if unidade not in respostas_map:
            respostas_map[unidade] = {"confirmados": [], "recusados": [], "sem_resposta": []}

        if r["status"] == "confirmed":
            respostas_map[unidade]["confirmados"].append(r["nome"])
        elif r["status"] == "declined":
            respostas_map[unidade]["recusados"].append(r["nome"])
        else:
            respostas_map[unidade]["sem_resposta"].append(r["nome"])

    return jsonify({
        "pendentes": [{"unidade": u, "pessoas": p} for u, p in pendentes_map.items()],
        "respostas": [{"unidade": u, **v} for u, v in respostas_map.items()]
    })


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
