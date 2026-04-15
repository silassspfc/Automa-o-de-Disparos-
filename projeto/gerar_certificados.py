
import os
import smtplib
import argparse
import pandas as pd
from PIL import Image, ImageDraw, ImageFont
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders
from dotenv import load_dotenv
import logging

load_dotenv()

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(LOG_DIR, "execucao.log"), encoding="utf-8"),
    ]
)

# ─────────────────────────────────────────
# CAMINHOS
# ─────────────────────────────────────────
EXCEL_PATH = os.getenv("EXCEL_PATH")
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
TEMPLATE   = os.path.join(BASE_DIR, "assets", "certificado.png")
OUTPUT_DIR = os.path.join(BASE_DIR, "certificados")

# ─────────────────────────────────────────
# CREDENCIAIS DE EMAIL (.env)
# ─────────────────────────────────────────
EMAIL_REMETENTE = os.getenv("EMAIL_REMETENTE")
EMAIL_SENHA     = os.getenv("EMAIL_SENHA")

# ─────────────────────────────────────────
# CAMPOS DO CERTIFICADO
#   "caixa": (x1, y1, x2, y2) → texto centralizado dentro do retângulo
#   "posicao": (x, y)         → texto a partir do ponto
# ─────────────────────────────────────────
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


FONTS_DIR = os.path.join(BASE_DIR, "assets", "fonts")

def carregar_fonte(tamanho, negrito):
    fontes = (
        [
            os.path.join(FONTS_DIR, "arialbd.ttf"),   # bundlada no projeto (prioridade)
            "C:/Windows/Fonts/arialbd.ttf",            # fallback Windows
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",  # fallback Linux
        ]
        if negrito else
        [
            os.path.join(FONTS_DIR, "arial.ttf"),
            "C:/Windows/Fonts/arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]
    )
    for caminho in fontes:
        if os.path.exists(caminho):
            return ImageFont.truetype(caminho, tamanho)
    logging.warning("Nenhuma fonte TTF encontrada — usando fonte padrão.")
    return ImageFont.load_default()


def gerar_certificado(dados):
    imagem = Image.open(TEMPLATE).convert("RGBA")
    draw   = ImageDraw.Draw(imagem)

    for campo, cfg in CAMPOS.items():
        fonte = carregar_fonte(cfg["tamanho_fonte"], cfg["negrito"])
        texto = str(dados.get(campo, ""))

        if "caixa" in cfg:
            x1, y1, x2, y2 = cfg["caixa"]
            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
            draw.text((cx, cy), texto, font=fonte, fill=cfg["cor"], anchor="mm")
        else:
            draw.text(cfg["posicao"], texto, font=fonte, fill=cfg["cor"])

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    nome_arquivo = f"{dados['Nome Completo'].replace(' ', '_')}_{dados['Data'].replace('/', '-')}.pdf"
    caminho = os.path.join(OUTPUT_DIR, nome_arquivo)
    imagem.convert("RGB").save(caminho, format="PDF")
    return caminho


def enviar_email(destinatario, nome, treinamento, caminho_pdf):
    msg = MIMEMultipart()
    msg["From"]    = EMAIL_REMETENTE
    msg["To"]      = destinatario
    msg["Subject"] = f"Certificado de Treinamento — {treinamento}"

    corpo = f"""Olá, {nome}!

Segue em anexo o seu certificado de conclusão do treinamento "{treinamento}".

Parabéns pela participação!

Atenciosamente,
Onodera Estética"""

    msg.attach(MIMEText(corpo, "plain", "utf-8"))

    with open(caminho_pdf, "rb") as f:
        parte = MIMEBase("application", "octet-stream")
        parte.set_payload(f.read())
        encoders.encode_base64(parte)
        parte.add_header("Content-Disposition", f'attachment; filename="{os.path.basename(caminho_pdf)}"')
        msg.attach(parte)

    with smtplib.SMTP("smtp.office365.com", 587) as servidor:
        servidor.starttls()
        servidor.login(EMAIL_REMETENTE, EMAIL_SENHA)
        servidor.sendmail(EMAIL_REMETENTE, destinatario, msg.as_string())


def main(dry_run=False):
    if dry_run:
        logging.info("MODO DRY-RUN — nenhum arquivo será gerado e nenhum email será enviado.")

    if not os.path.exists(EXCEL_PATH):
        logging.error(f"Excel não encontrado: {EXCEL_PATH}")
        return

    # Leitura única no início
    df = pd.read_excel(EXCEL_PATH)
    df.columns = df.columns.str.strip()  # remove espaços invisíveis nos cabeçalhos

    # Validação das colunas obrigatórias
    colunas_obrigatorias = {"ID", "Nome Completo", "Qual o Treinamento de Hoje?", "Qual seu email", "Data de hoje"}
    colunas_ausentes = colunas_obrigatorias - set(df.columns)
    if colunas_ausentes:
        logging.error(
            f"Colunas não encontradas na planilha: {', '.join(sorted(colunas_ausentes))}\n"
            f"  Colunas disponíveis: {', '.join(df.columns)}"
        )
        return

    df["ID"] = df["ID"].astype(str)

    # Garante que a coluna de controle existe
    if "Certificado" not in df.columns:
        df["Certificado"] = ""

    gerados         = 0
    enviados        = 0
    ignorados       = 0
    houve_alteracao = False
    erros           = []   # (nome, motivo)
    detalhes        = []   # {nome, treinamento, status} — para tabela no Streamlit

    for idx, linha in df.iterrows():
        if str(linha.get("Certificado", "")).strip() == "Enviado":
            ignorados += 1
            continue

        email = str(linha.get("Qual seu email", "")).strip()
        dados = {
            "Nome Completo": str(linha["Nome Completo"]).strip(),
            "Treinamento":   str(linha["Qual o Treinamento de Hoje?"]).strip(),
            "Data":          pd.to_datetime(linha["Data de hoje"]).strftime("%d/%m/%Y") if pd.notna(linha["Data de hoje"]) else "",
        }

        try:
            if not dry_run:
                caminho = gerar_certificado(dados)
            gerados += 1

            if email and "@" in email:
                if not dry_run:
                    enviar_email(email, dados["Nome Completo"], dados["Treinamento"], caminho)
                    df.loc[idx, "Certificado"] = "Enviado"
                    houve_alteracao = True
                enviados += 1
                detalhes.append({"Nome": dados["Nome Completo"], "Treinamento": dados["Treinamento"], "Status": "✓ Enviado"})
            else:
                motivo = f"email inválido: '{email}'"
                erros.append((dados["Nome Completo"], motivo))
                detalhes.append({"Nome": dados["Nome Completo"], "Treinamento": dados["Treinamento"], "Status": f"✗ {motivo}"})

        except Exception as e:
            erros.append((dados["Nome Completo"], str(e)))
            detalhes.append({"Nome": dados["Nome Completo"], "Treinamento": dados["Treinamento"], "Status": f"✗ Erro: {e}"})

    # Gravação única no final, só se houve alguma alteração
    if not dry_run and houve_alteracao:
        df.to_excel(EXCEL_PATH, index=False)

    # ── Relatório no log ─────────────────────────────────────────────────────
    linhas_erro = "\n".join(f"      → {nome}: {motivo}" for nome, motivo in erros)
    logging.info(
        f"\n  {'─'*45}\n"
        f"  RESUMO DA EXECUÇÃO\n"
        f"  {'─'*45}\n"
        f"  ✓ Certificados gerados:  {gerados}\n"
        f"  ✓ Emails enviados:       {enviados}\n"
        f"  ✗ Erros:                 {len(erros)}"
        + (f"\n{linhas_erro}" if erros else "") +
        f"\n  ○ Já processados:        {ignorados} (ignorados)\n"
        f"  {'─'*45}"
    )

    return {
        "gerados":   gerados,
        "enviados":  enviados,
        "ignorados": ignorados,
        "erros":     erros,
        "detalhes":  detalhes,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Simula a execução sem gerar arquivos nem enviar emails.")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
