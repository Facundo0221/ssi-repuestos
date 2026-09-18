import re

# Detecta < o > (los caracteres que crean HTML)
_SOSPECHOSO = re.compile(r"[<>]")

def limpiar_texto(valor):
    """Rechaza texto con < o >, y limpia espacios."""
    if valor is None:
        return valor
    if _SOSPECHOSO.search(valor):
        raise ValueError("El campo no puede contener < ni >")
    return valor.strip()

def limpiar_url(valor):
    """Solo acepta URLs http:// o https://."""
    if valor is None or valor == "":
        return valor
    if not (valor.startswith("http://") or valor.startswith("https://")):
        raise ValueError("La URL debe empezar con http:// o https://")
    if _SOSPECHOSO.search(valor):
        raise ValueError("La URL contiene caracteres no permitidos")
    return valor
