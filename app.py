import os
from flask import Flask, request, jsonify
from datetime import datetime, timezone
from services.supabase_client import client
from services.whatsapp import GESTOR_NUMBER, AGENTE_AUTORIZADOS, _send
from services.agent import process_gestor_message
from services.tally import achar

app = Flask(__name__)

TRAINING_LABELS = ("online", "prescencial", "presencial")


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

    # SIM/NÃO → confirmação de presença em treinamentos presenciais
    mensagem_upper = mensagem.upper()
    if mensagem_upper not in ("SIM", "NÃO", "NAO"):
        return jsonify({"ok": True}), 200

    status = "confirmed" if mensagem_upper == "SIM" else "declined"

    result = (
        client.table("treinamentos")
        .select("id")
        .eq("telefone_responsavel", telefone)
        .eq("confirmacao_status", "sent")
        .execute()
    )

    if not result.data:
        return jsonify({"ok": True}), 200

    client.table("treinamentos").update({
        "confirmacao_status": status
    }).eq("telefone_responsavel", telefone).eq("confirmacao_status", "sent").execute()

    print(f"[CONFIRMAÇÃO] {telefone} respondeu {mensagem_upper} → {status} ({len(result.data)} inscrito(s))")
    return jsonify({"ok": True}), 200


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
                print(f"[TREINAMENTO] Encontrado pelo formId {form_id}: {treinamentos_selecionados[0]}")

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

        unidade_result = (
            client.table("unidades")
            .select("telefone_responsavel")
            .eq("nome", unidade)
            .limit(1)
            .execute()
        )
        telefone_responsavel = unidade_result.data[0]["telefone_responsavel"] if unidade_result.data else None

        record = client.table("treinamentos").insert({
            "nome":                 nome,
            "email":                email or None,
            "crm":                  crm or None,
            "treinamento":          treinamento,
            "data_treinamento":     data_tr,
            "unidade":              unidade,
            "telefone_responsavel": telefone_responsavel,
        }).execute()

        ids_salvos.append(record.data[0]["id"])
        print(f"[TREINAMENTO] Salvo: {nome} | {treinamento} | {data_tr} | id {record.data[0]['id']}")

    return jsonify({"ok": True, "ids": ids_salvos}), 200


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
