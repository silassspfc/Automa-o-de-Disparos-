import os
import io
import uuid
import smtplib
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders
from services.supabase_client import client as supabase

BASE_DIR  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE  = os.path.join(BASE_DIR, "projeto", "assets", "certificado.png")
FONTS_DIR = os.path.join(BASE_DIR, "projeto", "assets", "fonts")

EMAIL_REMETENTE = os.getenv("EMAIL_REMETENTE")
EMAIL_SENHA     = os.getenv("EMAIL_SENHA")

# Mesmos campos do gerar_Certificados.py original
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


def _enviar_email(destinatario: str, nome: str, treinamento: str, pdf_bytes: bytes, nome_arquivo: str):
    msg           = MIMEMultipart()
    msg["From"]   = EMAIL_REMETENTE
    msg["To"]     = destinatario
    msg["Subject"] = f"Certificado de Treinamento — {treinamento}"

    msg.attach(MIMEText(
        f"Olá, {nome}!\n\n"
        f"Segue em anexo o seu certificado de conclusão do treinamento \"{treinamento}\".\n\n"
        f"Parabéns pela participação!\n\nAtenciosamente,\nOnodera Estética",
        "plain", "utf-8"
    ))

    parte = MIMEBase("application", "octet-stream")
    parte.set_payload(pdf_bytes)
    encoders.encode_base64(parte)
    parte.add_header("Content-Disposition", f'attachment; filename="{nome_arquivo}"')
    msg.attach(parte)

    with smtplib.SMTP("smtp.office365.com", 587, timeout=30) as s:
        s.starttls()
        s.login(EMAIL_REMETENTE, EMAIL_SENHA)
        s.sendmail(EMAIL_REMETENTE, destinatario, msg.as_string())


def gerar_e_enviar_certificados(data: str) -> str:
    """
    Gera e envia Certificados para todos os inscritos de uma data (YYYY-MM-DD)
    que ainda não receberam. Retorna resumo em texto para o agente.
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

    enviados, sem_email, erros = [], [], []

    for r in registros:
        nome        = r["nome"]
        email       = r.get("email") or ""
        treinamento = r["treinamento"]
        rid         = r["id"]

        try:
            pdf_bytes    = _gerar_pdf_bytes(nome, data_fmt)
            nome_arquivo = f"{nome.replace(' ', '_')}_{data.replace('-', '')}_{uuid.uuid4().hex[:6]}.pdf"

            # Tenta upload no Storage (opcional — não trava se falhar)
            url = None
            try:
                url = _upload_storage(pdf_bytes, nome_arquivo)
            except Exception as e_storage:
                print(f"[CERTIFICADO] Storage falhou para {nome}, seguindo sem URL: {e_storage}")

            if email and "@" in email:
                _enviar_email(email, nome, treinamento, pdf_bytes, nome_arquivo)
                enviados.append(f"{r.get('unidade', '')} - {nome}")
            else:
                sem_email.append(f"{r.get('unidade', '')} - {nome} (sem email)")

            supabase.table("treinamentos").update({
                "certificado_enviado": True,
                "certificado_url":     url
            }).eq("id", rid).execute()

        except Exception as e:
            erros.append(f"{nome}: {e}")
            print(f"[CERTIFICADO] Erro para {nome}: {e}")

    linhas = [f"Certificados — {data_fmt}"]
    if enviados:
        linhas.append(f"\n{len(enviados)} enviado(s) por email:")
        linhas += [f"  ✓ {e}" for e in enviados]
    if sem_email:
        linhas.append(f"\n{len(sem_email)} gerado(s) sem email:")
        linhas += [f"  ○ {e}" for e in sem_email]
    if erros:
        linhas.append(f"\n{len(erros)} erro(s):")
        linhas += [f"  ✗ {e}" for e in erros]

    return "\n".join(linhas)
