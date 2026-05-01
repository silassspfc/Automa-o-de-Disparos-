import os
import re
import logging
from flask import Flask, request, jsonify
from datetime import date
from services.supabase_client import client
from services.whatsapp import GESTOR_NUMBER, AGENTE_AUTORIZADOS, _send
from services.agent import process_gestor_message
from services.tally import achar, achar_checkboxes
from services.recrutamento import processar_comportamental
from services.constants import CONFIRM_SENT, CONFIRM_CONFIRMED, CONFIRM_DECLINED

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

app = Flask(__name__)

TRAINING_LABELS = ("online", "prescencial", "presencial")


def _extrair_data_do_nome(nome: str) -> str | None:
    """Extrai data do nome do treinamento no formato 'DD.MM - ...' → 'YYYY-MM-DD'."""
    match = re.match(r'^(\d{2})\.(\d{2})', nome.strip())
    if match:
        dia, mes = match.groups()
        return f"{date.today().year}-{mes}-{dia}"
    return None


@app.route("/webhook/whatsapp", methods=["POST"])
def receive_reply():
    """Recebe mensagens do número bot via Agile Talk."""
    data = request.json

    if data.get("method") == "message_sent_waba":
        return jsonify({"ok": True}), 200

    telefone = data.get("ticket", {}).get("contact", {}).get("number", "")
    mensagem = (data.get("msg", {}).get("body") or "").strip()

    if not telefone:
        log.warning("Payload sem telefone ignorado")
        return jsonify({"ok": True}), 200

    if telefone in AGENTE_AUTORIZADOS or (GESTOR_NUMBER and telefone == GESTOR_NUMBER):
        log.info(f"Mensagem recebida de {telefone}: {mensagem}")
        try:
            resposta = process_gestor_message(mensagem)
            _send(GESTOR_NUMBER, resposta)
            log.info(f"Resposta enviada: {resposta}")
        except Exception as e:
            log.error(f"Erro ao processar mensagem do gestor: {e}")
        return jsonify({"ok": True}), 200

    # SIM/NÃO → confirmação de presença em treinamentos presenciais
    mensagem_upper = mensagem.upper()
    if mensagem_upper not in ("SIM", "NÃO", "NAO"):
        return jsonify({"ok": True}), 200

    status = CONFIRM_CONFIRMED if mensagem_upper == "SIM" else CONFIRM_DECLINED

    result = (
        client.table("treinamentos")
        .select("id")
        .eq("telefone_responsavel", telefone)
        .eq("confirmacao_status", CONFIRM_SENT)
        .execute()
    )

    if not result.data:
        return jsonify({"ok": True}), 200

    client.table("treinamentos").update({
        "confirmacao_status": status
    }).eq("telefone_responsavel", telefone).eq("confirmacao_status", CONFIRM_SENT).execute()

    log.info(f"Confirmação: {telefone} respondeu {mensagem_upper} → {status} ({len(result.data)} inscrito(s))")
    return jsonify({"ok": True}), 200


@app.route("/webhook/treinamento", methods=["POST"])
def receive_treinamento():
    """Recebe inscrição de treinamento via Tally ou payload flat."""
    payload = request.json
    log.info("Treinamento — payload recebido")

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
                log.info(f"Treinamento — encontrado pelo formId {form_id}: {treinamentos_selecionados[0]}")

    if not nome or not treinamentos_selecionados:
        log.warning(f"Treinamento — campos ausentes: nome={nome} treinamentos={treinamentos_selecionados}")
        return jsonify({"error": "Campos obrigatórios ausentes: nome, treinamento"}), 400

    unidade_result = (
        client.table("unidades")
        .select("telefone_responsavel")
        .eq("nome", unidade)
        .limit(1)
        .execute()
    )
    telefone_responsavel = unidade_result.data[0]["telefone_responsavel"] if unidade_result.data else None

    ids_salvos = []
    for treinamento in treinamentos_selecionados:
        cron = (
            client.table("cronograma")
            .select("data")
            .eq("treinamento", treinamento)
            .limit(1)
            .execute()
        )
        if cron.data:
            data_tr = cron.data[0]["data"]
        else:
            data_tr = _extrair_data_do_nome(treinamento)
            if data_tr:
                log.info(f"Treinamento — data extraída do nome: {data_tr} para '{treinamento}'")
            else:
                log.warning(f"Treinamento — data não encontrada para '{treinamento}'")
        log.info(f"Treinamento — data final: {data_tr} para '{treinamento}'")

        # Dedupe: ignora se já existe inscrição igual
        existing = (
            client.table("treinamentos")
            .select("id")
            .eq("nome", nome)
            .eq("treinamento", treinamento)
            .eq("data_treinamento", data_tr)
            .limit(1)
            .execute()
        )
        if existing.data:
            log.info(f"Inscrição duplicada ignorada: {nome} | {treinamento} | {data_tr}")
            ids_salvos.append(existing.data[0]["id"])
            continue

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
        log.info(f"Treinamento salvo: {nome} | {treinamento} | {data_tr} | id {record.data[0]['id']}")

    return jsonify({"ok": True, "ids": ids_salvos}), 200


def _get_file_url(fields: list, keyword: str) -> str:
    for f in fields:
        if keyword.lower() not in f["label"].lower():
            continue
        valor = f.get("value")
        if isinstance(valor, list) and valor:
            first = valor[0]
            return first.get("url", "") if isinstance(first, dict) else str(first)
        return str(valor).strip() if valor else ""
    return ""



@app.route("/webhook/candidatura", methods=["POST"])
def receive_candidatura():
    """Recebe inscrição de candidato via Tally (formulário de currículo)."""
    payload = request.json
    log.info("Candidatura — payload recebido")

    fields = payload.get("data", {}).get("fields", []) if "data" in payload else []

    if fields:
        nome     = achar(fields, "nome",     exclude_parens=True)
        telefone = achar(fields, "telefone", exclude_parens=True)
        email    = achar(fields, "email",    exclude_parens=True)
        regioes  = achar_checkboxes(fields, "região") or achar_checkboxes(fields, "regiao")
        regiao   = ", ".join(regioes) if regioes else achar(fields, "região", exclude_parens=True)
        vagas    = achar_checkboxes(fields, "vaga")
        cv_url   = _get_file_url(fields, "curriculo") or _get_file_url(fields, "currículo")
    else:
        nome     = payload.get("nome", "").strip()
        telefone = payload.get("telefone", "").strip()
        email    = payload.get("email", "").strip()
        regiao   = payload.get("regiao", "").strip()
        vagas    = [payload.get("vaga", "").strip()]
        cv_url   = payload.get("cv_url", "").strip()

    if not nome or not vagas:
        log.warning(f"Candidatura — campos ausentes: nome={nome} vagas={vagas}")
        return jsonify({"error": "Campos obrigatórios: nome, vaga"}), 400

    ids_salvos = []
    for vaga_str in vagas:
        # ilike parcial: "Consultora de Vendas" bate em "Consultora", etc.
        vaga_r  = client.table("vagas").select("id").ilike("titulo", f"%{vaga_str.split()[0]}%").limit(1).execute()
        vaga_id = vaga_r.data[0]["id"] if vaga_r.data else None

        record       = client.table("candidatos").insert({
            "nome":     nome,
            "telefone": telefone or None,
            "email":    email or None,
            "regiao":   regiao or None,
            "vaga_id":  vaga_id,
            "cv_url":   cv_url or None,
        }).execute()
        candidato_id = record.data[0]["id"]
        ids_salvos.append(candidato_id)
        log.info(f"Candidatura salva: {nome} | vaga={vaga_str} | vaga_id={vaga_id} | id={candidato_id}")

    return jsonify({"ok": True, "ids": ids_salvos}), 200


@app.route("/webhook/comportamental", methods=["POST"])
def receive_comportamental():
    """Recebe respostas do formulário comportamental via Tally."""
    payload = request.json
    log.info("Comportamental — payload recebido")

    fields = payload.get("data", {}).get("fields", []) if "data" in payload else []

    if fields:
        telefone = achar(fields, "telefone", exclude_parens=True)
        email    = achar(fields, "email",    exclude_parens=True)
    else:
        telefone = payload.get("telefone", "").strip()
        email    = payload.get("email", "").strip()

    candidato = None
    if telefone:
        r = client.table("candidatos").select("id").eq("telefone", telefone).order("created_at", desc=True).limit(1).execute()
        if r.data:
            candidato = r.data[0]
    if not candidato and email:
        r = client.table("candidatos").select("id").eq("email", email).order("created_at", desc=True).limit(1).execute()
        if r.data:
            candidato = r.data[0]

    if not candidato:
        log.warning(f"Comportamental — candidato não encontrado: telefone={telefone} email={email}")
        return jsonify({"error": "Candidato não encontrado"}), 404

    IGNORAR = {"telefone", "email", "nome"}
    respostas = {}
    if fields:
        for f in fields:
            label = f["label"].strip()
            valor = f.get("value")
            if valor and not any(k in label.lower() for k in IGNORAR):
                respostas[label] = str(valor).strip()
    else:
        respostas = {k: v for k, v in payload.items() if k not in IGNORAR and v}

    processar_comportamental(candidato["id"], respostas)
    return jsonify({"ok": True}), 200


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
