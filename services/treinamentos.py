from datetime import datetime
from services.supabase_client import client
from services.whatsapp import _send


def preview_confirmacao(data: str) -> str:
    """Mostra quais unidades receberão a mensagem de confirmação, sem enviar."""
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
        .select("nome, unidade, telefone_responsavel")
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

    grupos, sem_telefone = {}, []
    for r in registros:
        telefone = r.get("telefone_responsavel") or ""
        unidade  = r.get("unidade") or "Sem unidade"
        if not telefone:
            sem_telefone.append(f"{unidade} — {r['nome']}")
            continue
        grupos.setdefault(unidade, []).append(r["nome"])

    linhas = [f"Preview — confirmações a enviar em {data_fmt}\n"]
    for unidade, nomes in grupos.items():
        linhas.append(f"• {unidade} ({len(nomes)} pessoa(s)):")
        linhas += [f"    - {n}" for n in nomes]
    if sem_telefone:
        linhas.append(f"\nSem telefone cadastrado ({len(sem_telefone)}):")
        linhas += [f"  ○ {n}" for n in sem_telefone]
    linhas.append("\nPara enviar, responda: pode enviar")
    return "\n".join(linhas)


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


def ativar_treinamento(data: str) -> str:
    """Envia mensagem de ativação para o grupo geral dos treinamentos de uma data."""
    cron = (
        client.table("cronograma")
        .select("treinamento, link_inscricao, numero_grupo")
        .eq("data", data)
        .neq("tipo", "online")
        .execute()
    )

    registros = [r for r in (cron.data or []) if r.get("numero_grupo")]

    if not cron.data:
        return f"Nenhum treinamento presencial em {data}."
    if not registros:
        return f"Nenhum treinamento com grupo configurado para {data}. Preencha numero_grupo e link_inscricao no cronograma."

    try:
        data_fmt = datetime.strptime(data, "%Y-%m-%d").strftime("%d/%m/%Y")
    except Exception:
        data_fmt = data

    enviados, erros = [], []

    for r in registros:
        nome_tr = r["treinamento"]
        grupo   = r["numero_grupo"]
        link    = r.get("link_inscricao") or ""

        mensagem = (
            f"Boa tarde, rede Onodera!\n"
            f"Passando para reforçar a participação no *{nome_tr}*!\n"
            f"Contamos com a presença de vocês!"
        )
        if link:
            mensagem += f"\n\nInscrições: {link}"

        try:
            _send(grupo, mensagem)
            enviados.append(nome_tr)
            print(f"[ATIVAÇÃO] Enviado para grupo {grupo}: {nome_tr}")
        except Exception as e:
            erros.append(f"{nome_tr}: {e}")
            print(f"[ATIVAÇÃO] Erro ao enviar {nome_tr}: {e}")

    linhas = [f"Ativação — {data_fmt}"]
    if enviados:
        linhas.append(f"\n{len(enviados)} treinamento(s) ativado(s):")
        linhas += [f"  ✓ {t}" for t in enviados]
    if erros:
        linhas.append(f"\nErros:")
        linhas += [f"  ✗ {e}" for e in erros]

    return "\n".join(linhas)
