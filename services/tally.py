def achar(fields: list, keyword: str, exclude_parens: bool = False) -> str:
    """Busca campo pelo label no payload Tally. Resolve DROPDOWN por ID."""
    for f in fields:
        if keyword.lower() not in f["label"].lower():
            continue
        if exclude_parens and "(" in f["label"]:
            continue
        tipo  = f.get("type", "")
        valor = f.get("value")
        if tipo == "DROPDOWN" and isinstance(valor, list):
            selected = [o["text"] for o in f.get("options", []) if o["id"] in valor]
            return selected[0] if selected else ""
        if valor is None:
            return ""
        return str(valor).strip()
    return ""


def achar_checkboxes(fields: list, keyword: str) -> list[str]:
    """Retorna textos selecionados de campo CHECKBOXES, ignorando campos expandidos."""
    for f in fields:
        if keyword.lower() not in f["label"].lower():
            continue
        if "(" in f["label"]:
            continue
        if f.get("type") == "CHECKBOXES" and isinstance(f.get("value"), list):
            return [o["text"].strip() for o in f.get("options", []) if o["id"] in f["value"]]
    return []
