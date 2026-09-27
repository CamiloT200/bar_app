import unicodedata


def normalizar_nombre(texto):
    """"  Águila  Negra" -> "aguila negra": sin tildes, sin mayúsculas, espacios simples.

    Sirve para comparar nombres y para ordenarlos: SQLite ordena por código de
    carácter y pondría "Águila" después de "Zapote".
    """
    sin_tildes = "".join(
        c for c in unicodedata.normalize("NFD", texto) if not unicodedata.combining(c)
    )
    return " ".join(sin_tildes.casefold().split())


def ordenar_por_nombre(objetos, atributo="nombre"):
    return sorted(objetos, key=lambda o: normalizar_nombre(getattr(o, atributo)))
