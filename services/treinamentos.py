from datetime import datetime
from services.supabase_client import client
from services.whatsapp import _send


def confirmar_presenca(data: str) -> str:
    """Envia confirmação de presença para inscritos em treinamentos presenciais de uma data."""
    cron = (
        client.table("cronograma")
        .select("treinamento")
        .eq("data", data)
        .neq("tipo", "online")
        .execute()
    )

    treinamentos_presenciais = [r["treinamento"] for r in (cron.data or [])]
    if not treinamentos_presenciais:
        return f"Nenhum treinamento presencial em {data}."

    result = (
        client.table("treinamentos")
        .select("*")
        .eq("data_treinamento", data)
        .in_("treinamento", treinamentos_presenciais)
        .is_("confirmacao_status", "null")
        .execute()
    )

    registros = result.data or []
    if not registros:
        return f"Nenhum inscrito pendente de confirmação para {data}."

    try:
        data_fmt = datetime.strptime(data, "%Y-%m-%d").strftime("%d/%m/%Y")
    except Exception:
        data_fmt = data

    grupos      = {}
    sem_telefone = []

    for r in registros:
        telefone = r.get("telefone_responsavel") or ""
        unidade  = r.get("unidade") or "Sem unidade"
        if not telefone:
            sem_telefone.append(r["nome"])
            continue
        chave = (unidade, telefone)
        grupos.setdefault(chave, {"nomes": [], "ids": []})
        grupos[chave]["nomes"].append(r["nome"])
        grupos[chave]["ids"].append(r["id"])

    enviados, erros = [], []

    for (unidade, telefone), dados in grupos.items():
        nomes_lista = "\n".join(f"• {n}" for n in dados["nomes"])
        mensagem = (
            f"Treinamento — {data_fmt}\n"
            f"Unidade: *{unidade}*\n\n"
            f"Os seguintes inscritos confirmarão presença?\n"
            f"{nomes_lista}\n\n"
            f"Responda *SIM* para confirmar ou *NÃO* para recusar."
        )
        try:
            _send(telefone, mensagem)
            for rid in dados["ids"]:
                client.table("treinamentos").update({
                    "confirmacao_status": "sent"
                }).eq("id", rid).execute()
            enviados.append(f"{unidade} ({len(dados['nomes'])} pessoa(s))")
            print(f"[CONFIRMAÇÃO] Enviado para {unidade} ({telefone})")
        except Exception as e:
            erros.append(f"{unidade}: {e}")
            print(f"[CONFIRMAÇÃO] Erro para {unidade}: {e}")

    linhas = [f"Confirmações enviadas — {data_fmt}"]
    if enviados:
        linhas.append(f"\n{len(enviados)} unidade(s) notificadas:")
        linhas += [f"  ✓ {e}" for e in enviados]
    if sem_telefone:
        linhas.append(f"\n{len(sem_telefone)} inscrito(s) sem telefone na unidade:")
        linhas += [f"  ○ {n}" for n in sem_telefone]
    if erros:
        linhas.append(f"\nErros:")
        linhas += [f"  ✗ {e}" for e in erros]

    return "\n".join(linhas)


def relatorio_confirmacoes(data: str) -> str:
    """Retorna relatório de confirmados, recusados e sem resposta de uma data."""
    result = (
        client.table("treinamentos")
        .select("nome, unidade, treinamento, confirmacao_status")
        .eq("data_treinamento", data)
        .in_("confirmacao_status", ["sent", "confirmed", "declined"])
        .execute()
    )

    registros = result.data or []
    if not registros:
        return f"Nenhuma confirmação enviada para {data}."

    try:
        data_fmt = datetime.strptime(data, "%Y-%m-%d").strftime("%d/%m/%Y")
    except Exception:
        data_fmt = data

    confirmados = [r for r in registros if r["confirmacao_status"] == "confirmed"]
    recusados   = [r for r in registros if r["confirmacao_status"] == "declined"]
    pendentes   = [r for r in registros if r["confirmacao_status"] == "sent"]

    linhas = [f"Confirmações — {data_fmt}"]
    if confirmados:
        linhas.append(f"\n✓ Confirmados ({len(confirmados)}):")
        linhas += [f"  {r['unidade']} — {r['nome']}" for r in confirmados]
    if recusados:
        linhas.append(f"\n✗ Recusados ({len(recusados)}):")
        linhas += [f"  {r['unidade']} — {r['nome']}" for r in recusados]
    if pendentes:
        linhas.append(f"\n○ Sem resposta ({len(pendentes)}):")
        linhas += [f"  {r['unidade']} — {r['nome']}" for r in pendentes]

    return "\n".join(linhas)
