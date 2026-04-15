import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import sys
import os
from dotenv import load_dotenv, set_key, dotenv_values

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")

sys.path.insert(0, BASE_DIR)

BG       = "#ffffff"
VERDE    = "#2e7d32"
VERMELHO = "#c62828"
AZUL     = "#1a237e"
CINZA    = "#f0f0f0"


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def cabecalho(janela):
    cab = tk.Frame(janela, bg=AZUL)
    cab.pack(fill="x")
    tk.Label(cab, text="🎓  Certificados Onodera", font=("Segoe UI", 13, "bold"),
             bg=AZUL, fg="white", pady=12).pack()


def config_completa():
    """Retorna True se o .env já tem todos os campos preenchidos."""
    if not os.path.exists(ENV_PATH):
        return False
    vals = dotenv_values(ENV_PATH)
    return all(vals.get(k, "").strip() for k in ["EMAIL_REMETENTE", "EMAIL_SENHA", "EXCEL_PATH"])


def centralizar(janela, w, h):
    x = (janela.winfo_screenwidth()  - w) // 2
    y = (janela.winfo_screenheight() - h) // 2
    janela.geometry(f"{w}x{h}+{x}+{y}")


# ─────────────────────────────────────────────────────────────────────────────
# TELA DE CONFIGURAÇÃO
# ─────────────────────────────────────────────────────────────────────────────

def tela_configuracao(ao_salvar):
    janela = tk.Tk()
    janela.title("Configuração inicial")
    janela.configure(bg=BG)
    janela.resizable(False, False)
    centralizar(janela, 520, 420)
    cabecalho(janela)

    tk.Label(janela, text="Bem-vindo! Vamos configurar o sistema.", font=("Segoe UI", 12, "bold"),
             bg=BG, fg="#333").pack(pady=(20, 2))
    tk.Label(janela, text="Preencha os campos abaixo. Isso só é necessário uma vez.",
             font=("Segoe UI", 10), bg=BG, fg="#777").pack(pady=(0, 16))

    corpo = tk.Frame(janela, bg=BG)
    corpo.pack(padx=40, fill="x")

    def campo(label, mostrar=""):
        tk.Label(corpo, text=label, font=("Segoe UI", 10, "bold"), bg=BG, fg="#444",
                 anchor="w").pack(fill="x", pady=(8, 2))
        var = tk.StringVar()
        entry = tk.Entry(corpo, textvariable=var, font=("Segoe UI", 11),
                         relief="solid", bd=1, show=mostrar)
        entry.pack(fill="x", ipady=6)
        return var

    var_email = campo("📧  Email que vai enviar os certificados")
    var_senha = campo("🔑  Senha do email", mostrar="●")

    tk.Label(corpo, text="📁  Arquivo Excel com as respostas", font=("Segoe UI", 10, "bold"),
             bg=BG, fg="#444", anchor="w").pack(fill="x", pady=(8, 2))

    frame_excel = tk.Frame(corpo, bg=BG)
    frame_excel.pack(fill="x")
    var_excel = tk.StringVar()
    entry_excel = tk.Entry(frame_excel, textvariable=var_excel, font=("Segoe UI", 10),
                           relief="solid", bd=1, state="readonly")
    entry_excel.pack(side="left", fill="x", expand=True, ipady=6)

    def procurar():
        caminho = filedialog.askopenfilename(
            title="Selecione a planilha Excel",
            filetypes=[("Arquivos Excel", "*.xlsx *.xls")]
        )
        if caminho:
            var_excel.set(caminho.replace("/", "\\"))

    tk.Button(frame_excel, text="Procurar", command=procurar,
              font=("Segoe UI", 10), bg=CINZA, relief="flat",
              padx=10, cursor="hand2").pack(side="left", padx=(6, 0))

    def salvar():
        email = var_email.get().strip()
        senha = var_senha.get().strip()
        excel = var_excel.get().strip()

        if not email or not senha or not excel:
            messagebox.showwarning("Campos obrigatórios", "Por favor, preencha todos os campos.")
            return

        if "@" not in email:
            messagebox.showwarning("Email inválido", "Digite um endereço de email válido.")
            return

        # Salva no .env
        if not os.path.exists(ENV_PATH):
            open(ENV_PATH, "w").close()

        set_key(ENV_PATH, "EMAIL_REMETENTE", email)
        set_key(ENV_PATH, "EMAIL_SENHA",     senha)
        set_key(ENV_PATH, "EXCEL_PATH",      excel)

        janela.destroy()
        ao_salvar()

    tk.Button(janela, text="Salvar e continuar →", command=salvar,
              font=("Segoe UI", 12, "bold"), bg=AZUL, fg="white",
              relief="flat", padx=32, pady=10, cursor="hand2", bd=0).pack(pady=20)

    janela.mainloop()


# ─────────────────────────────────────────────────────────────────────────────
# TELA PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────

def tela_principal():
    load_dotenv(ENV_PATH, override=True)
    from gerar_certificados import main

    janela = tk.Tk()
    janela.title("Certificados Onodera")
    janela.configure(bg=BG)
    janela.resizable(False, False)
    centralizar(janela, 500, 220)
    cabecalho(janela)

    frame_loading  = tk.Frame(janela, bg=BG)
    frame_resultado = tk.Frame(janela, bg=BG)

    def executar():
        try:
            resultado = main(dry_run=False)
            janela.after(0, lambda: mostrar_resultado(janela, frame_loading, frame_resultado, resultado))
        except Exception as e:
            janela.after(0, lambda: mostrar_erro(janela, frame_loading, frame_resultado, traduzir_erro(str(e))))

    tk.Label(frame_loading, text="Enviando certificados...",
             font=("Segoe UI", 12), bg=BG, fg="#555").pack(pady=(24, 8))
    bar = ttk.Progressbar(frame_loading, mode="indeterminate", length=320)
    bar.pack()
    bar.start(10)
    frame_loading.pack(pady=4)

    threading.Thread(target=executar, daemon=True).start()
    janela.mainloop()


def traduzir_erro(msg):
    msg = msg.lower()
    if "permission denied" in msg or "permissionerror" in msg:
        return "O arquivo Excel está aberto. Feche-o e tente novamente."
    if "no such file" in msg or "não encontrado" in msg or "filenotfound" in msg:
        return "Não consegui encontrar o arquivo Excel. Verifique se ele ainda está no mesmo lugar."
    if "smtp" in msg or "login" in msg or "authentication" in msg:
        return "Não consegui conectar ao email. Verifique a senha e tente novamente."
    if "connectionerror" in msg or "timeout" in msg:
        return "Sem conexão com a internet. Verifique a rede e tente novamente."
    return "Algo deu errado. Tente novamente ou chame o suporte."


def mostrar_erro(janela, frame_loading, frame_resultado, msg):
    frame_loading.pack_forget()
    tk.Label(frame_resultado, text="❌", font=("Segoe UI", 48), bg=BG).pack(pady=(10, 0))
    tk.Label(frame_resultado, text="Algo deu errado", font=("Segoe UI", 16, "bold"),
             bg=BG, fg=VERMELHO).pack()
    tk.Label(frame_resultado, text=msg, font=("Segoe UI", 11),
             bg=BG, fg="#555", wraplength=420).pack(pady=(6, 16))
    tk.Button(frame_resultado, text="Fechar", command=janela.destroy,
              font=("Segoe UI", 12), bg=VERMELHO, fg="white",
              relief="flat", padx=28, pady=10, cursor="hand2", bd=0).pack()
    frame_resultado.pack(padx=32, pady=(0, 24))
    janela.update_idletasks()
    janela.geometry("")


def mostrar_resultado(janela, frame_loading, frame_resultado, r):
    frame_loading.pack_forget()

    enviados  = r["enviados"]
    erros     = r["erros"]
    ignorados = r["ignorados"]
    nenhum    = enviados == 0 and not erros

    if nenhum:
        icone, titulo, cor_titulo = "📭", "Nenhum certificado novo", "#757575"
    elif not erros:
        icone, titulo, cor_titulo = "✅", "Tudo enviado com sucesso!", VERDE
    else:
        icone, titulo, cor_titulo = "⚠️", "Concluído com atenção", "#e65100"

    tk.Label(frame_resultado, text=icone, font=("Segoe UI", 52), bg=BG).pack(pady=(8, 0))
    tk.Label(frame_resultado, text=titulo, font=("Segoe UI", 17, "bold"),
             bg=BG, fg=cor_titulo).pack()

    if not nenhum:
        resumo = tk.Frame(frame_resultado, bg=CINZA, padx=24, pady=14)
        resumo.pack(pady=14, ipadx=8)

        if enviados:
            tk.Label(resumo,
                     text=f"📧  {enviados} certificado{'s' if enviados > 1 else ''} enviado{'s' if enviados > 1 else ''} por email",
                     font=("Segoe UI", 12), bg=CINZA, fg=VERDE).pack(anchor="w", pady=2)
        if ignorados:
            tk.Label(resumo,
                     text=f"🔁  {ignorados} pessoa{'s' if ignorados > 1 else ''} já havia{'m' if ignorados > 1 else ''} recebido anteriormente",
                     font=("Segoe UI", 12), bg=CINZA, fg="#555").pack(anchor="w", pady=2)
        if erros:
            tk.Label(resumo,
                     text=f"❌  {len(erros)} não enviado{'s' if len(erros) > 1 else ''} — veja abaixo:",
                     font=("Segoe UI", 12), bg=CINZA, fg=VERMELHO).pack(anchor="w", pady=(8, 2))
            for nome, motivo in erros:
                tk.Label(resumo, text=f"      • {nome}: {motivo}",
                         font=("Segoe UI", 10), bg=CINZA, fg="#888").pack(anchor="w")
    else:
        tk.Label(frame_resultado, text="Não há novas respostas para processar.",
                 font=("Segoe UI", 11), bg=BG, fg="#888").pack(pady=4)

    tk.Button(frame_resultado, text="Fechar", command=janela.destroy,
              font=("Segoe UI", 12), bg=AZUL, fg="white",
              relief="flat", padx=32, pady=10, cursor="hand2", bd=0).pack(pady=(12, 4))

    frame_resultado.pack(padx=32, pady=(0, 24))
    janela.update_idletasks()
    janela.geometry("")


# ─────────────────────────────────────────────────────────────────────────────
# ENTRADA
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if config_completa():
        tela_principal()
    else:
        tela_configuracao(ao_salvar=tela_principal)
