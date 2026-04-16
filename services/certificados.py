import os
import io
import uuid
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
from services.supabase_client import client as supabase
from services.whatsapp import _send

BASE_DIR  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE  = os.path.join(BASE_DIR, "projeto", "assets", "certificado.png")
FONTS_DIR = os.path.join(BASE_DIR, "projeto", "assets", "fonts")

CAMPOS = {
    "Nome Completo": {
        "caixa": (1064, 1046, 2615, 1327),
        "tamanho_fonte": 100,
        "cor": (171, 20, 85),
        "negrito": True,
    },
    "Data": {
        "posicao": (270, 2001),
        "tamanho_fonte": 100,
        "cor": (171, 20, 85),
        "negrito": False,
    },
}


def _carregar_fonte(tamanho: int, negrito: bool):
    caminhos = (
        [os.path.join(FONTS_DIR, "arialbd.ttf"), "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
        if negrito else
        [os.path.join(FONTS_DIR, "arial.ttf"), "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    )
    for p in caminhos:
        if os.path.exists(p):
            return ImageFont.truetype(p, tamanho)
    return ImageFont.load_default()


def _gerar_pdf_bytes(nome: str, data_formatada: str) -> bytes:
    """Gera o certificado em memória e retorna os bytes do PDF."""
    imagem = Image.open(TEMPLATE).convert("RGBA")
    draw   = ImageDraw.Draw(imagem)

    dados = {"Nome Completo": nome, "Data": data_formatada}

    for campo, cfg in CAMPOS.items():
        fonte = _carregar_fonte(cfg["tamanho_fonte"], cfg["negrito"])
        texto = dados.get(campo, "")
        if "caixa" in cfg:
            x1, y1, x2, y2 = cfg["caixa"]
            draw.text(((x1 + x2) // 2, (y1 + y2) // 2), texto, font=fonte, fill=cfg["cor"], anchor="mm")
        else:
            draw.text(cfg["posicao"], texto, font=fonte, fill=cfg["cor"])

    buf = io.BytesIO()
    imagem.convert("RGB").save(buf, format="PDF")
    return buf.getvalue()


def _upload_storage(pdf_bytes: bytes, nome_arquivo: str) -> str:
    """Faz upload no Supabase Storage e retorna a URL pública."""
    supabase.storage.from_("Certificados").upload(
        path=nome_arquivo,
        file=pdf_bytes,
        file_options={"content-type": "application/pdf"}
    )
    return supabase.storage.from_("Certificados").get_public_url(nome_arquivo)


def _telefone_unidade(unidade: str) -> str:
    """Busca o telefone do responsável pela unidade."""
    result = (
        supabase.table("unidades")
        .select("telefone_responsavel")
        .eq("nome", unidade)
        .limit(1)
        .execute()
    )
    return result.data[0]["telefone_responsavel"] if result.data else ""


def gerar_e_enviar_certificados(data: str) -> str:
    """
    Gera certificados para todos os inscritos de uma data (YYYY-MM-DD),
    salva no Storage e envia 1 WhatsApp por unidade com os links.
    """
    result = (
        supabase.table("treinamentos")
        .select("*")
        .eq("data_treinamento", data)
        .eq("certificado_enviado", False)
        .execute()
    )

    registros = result.data or []
    if not registros:
        return f"Nenhum inscrito pendente de certificado para {data}."

    try:
        data_fmt = datetime.strptime(data, "%Y-%m-%d").strftime("%d/%m/%Y")
    except Exception:
        data_fmt = data

    # Agrupa por unidade: { unidade: [ {nome, url, rid}, ... ] }
    grupos = {}
    erros  = []

    for r in registros:
        nome      = r["nome"]
        unidade   = r.get("unidade") or "Sem unidade"
        rid       = r["id"]

        try:
            pdf_bytes    = _gerar_pdf_bytes(nome, data_fmt)
            nome_arquivo = f"{nome.replace(' ', '_')}_{data.replace('-', '')}_{uuid.uuid4().hex[:6]}.pdf"

            url = None
            try:
                url = _upload_storage(pdf_bytes, nome_arquivo)
            except Exception as e_storage:
                print(f"[CERTIFICADO] Storage falhou para {nome}: {e_storage}")

            grupos.setdefault(unidade, []).append({"nome": nome, "url": url, "rid": rid})

            supabase.table("treinamentos").update({
                "certificado_enviado": True,
                "certificado_url":     url
            }).eq("id", rid).execute()

        except Exception as e:
            erros.append(f"{nome}: {e}")
            print(f"[CERTIFICADO] Erro para {nome}: {e}")

    # Envia 1 WhatsApp por unidade
    enviados_wpp = []
    erros_wpp    = []

    for unidade, pessoas in grupos.items():
        telefone = _telefone_unidade(unidade)
        if not telefone:
            erros_wpp.append(f"{unidade} (sem telefone cadastrado)")
            continue

        linhas_msg = [f"Certificados — {data_fmt}\nUnidade: {unidade}\n"]
        for p in pessoas:
            if p["url"]:
                linhas_msg.append(f"• {p['nome']}\n{p['url']}")
            else:
                linhas_msg.append(f"• {p['nome']} (certificado gerado, link indisponível)")

        mensagem = "\n\n".join(linhas_msg)

        try:
            _send(telefone, mensagem)
            enviados_wpp.append(f"{unidade} ({len(pessoas)} pessoa(s))")
            print(f"[CERTIFICADO] WhatsApp enviado para {unidade} ({telefone})")
        except Exception as e_wpp:
            erros_wpp.append(f"{unidade}: {e_wpp}")
            print(f"[CERTIFICADO] Erro WhatsApp para {unidade}: {e_wpp}")

    # Resumo para o agente
    linhas = [f"Certificados — {data_fmt}"]
    if enviados_wpp:
        linhas.append(f"\n{len(enviados_wpp)} unidade(s) notificadas via WhatsApp:")
        linhas += [f"  ✓ {e}" for e in enviados_wpp]
    if erros_wpp:
        linhas.append(f"\n{len(erros_wpp)} unidade(s) sem envio:")
        linhas += [f"  ○ {e}" for e in erros_wpp]
    if erros:
        linhas.append(f"\n{len(erros)} erro(s) na geração:")
        linhas += [f"  ✗ {e}" for e in erros]

    return "\n".join(linhas)
